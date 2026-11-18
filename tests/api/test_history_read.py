"""历史读取与真实临时 SQLite 的兼容性测试(M0-R2 缺口 5)。

使用真实的 SqliteSaver 与真实 LangGraph 状态读取,不触碰业务数据库、
不调用真实模型:api 模块上的所有模型相关工厂一律打桩为“被调用即失败”,
证明历史读取不依赖聊天模型、工具注册表或记忆存储的初始化。

关键回归:加入一条有效的待应用写入(checkpoint_writes)后,
LangGraph 公开状态读取(graph.get_state)可见,而直接读
最新检查点的 channel_values 不可见 —— 历史接口必须与前者一致。
待应用写入按“读取图拓扑重算未处理任务”应用,因此参考实现与
被测端点都必须复用聊天图的公开构建入口,不手写内部合并协议。
"""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from job_agent.api import app as api_module
from job_agent.api.app import (
    Attachment,
    ChatRequest,
    build_thread_id,
    build_user_message,
    create_app,
)
from job_agent.graph import create_tool_call_graph
from job_agent.tools.factory import create_tool_registry

API_KEYS = '{"test-token":"user-001","other-token":"user-002"}'


def exploding(name: str):
    def factory(*args, **kwargs):
        raise AssertionError(f"{name} must not be called")

    return factory


class ScriptedModel:
    """脚本化假模型:无真实服务、无网络。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[list] = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, **kwargs):
        self.seen.append(list(messages))

        if not self.replies:
            raise AssertionError("scripted replies exhausted")

        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def configure_api_keys(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_API_KEYS", API_KEYS)


@pytest.fixture
def checkpoint_db(tmp_path, monkeypatch):
    """独立的检查点库 + 会话库;历史端点的模型/工具/记忆依赖被调用即失败。"""

    path = str(tmp_path / "checkpoints.sqlite")

    monkeypatch.setattr(api_module, "CHECKPOINT_DB", path)

    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    monkeypatch.setattr(
        api_module,
        "create_chat_model",
        exploding("create_chat_model"),
    )

    monkeypatch.setattr(
        api_module,
        "create_tool_registry",
        exploding("create_tool_registry"),
    )

    monkeypatch.setattr(
        api_module,
        "open_memory_store",
        exploding("open_memory_store"),
    )

    monkeypatch.setattr(
        api_module,
        "create_tool_call_graph",
        exploding("create_tool_call_graph"),
    )

    return path


@pytest.fixture
def client():
    def unused_handler(request, user_id):
        raise AssertionError("chat handler must not be used by history read")

    return TestClient(create_app(unused_handler))


def create_session(client, api_key="test-token", title="History test"):
    response = client.post(
        "/sessions",
        json={"title": title},
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 201

    return response.json()["conversation_id"]


def thread_config(user_id, conversation_id):
    return {
        "configurable": {
            "thread_id": build_thread_id(user_id, conversation_id),
        }
    }


def seed_conversation(checkpoint_db, user_id, conversation_id, turns):
    """用真实图 + 假模型写入对话;每轮独立开/关检查点连接(关而重开)。"""

    for human, replies in turns:
        message = (
            human if isinstance(human, HumanMessage) else HumanMessage(content=human)
        )

        with SqliteSaver.from_conn_string(checkpoint_db) as saver:
            saver.setup()

            graph = create_tool_call_graph(
                model=ScriptedModel(replies),
                registry=create_tool_registry(),
                checkpointer=saver,
            )

            graph.invoke(
                {"messages": [message]},
                thread_config(user_id, conversation_id),
            )


def completed_two_turns():
    return [
        ("我的第一问", [AIMessage(content="第一答。")]),
        ("我的第二问", [AIMessage(content="第二答。")]),
    ]


def test_thread_id_encoding_is_stable():
    """线程 ID 编码约定:JSON 数组 [user_id, conversation_id]。

    所有 chat/session 端点共用该编码;任何改动都会割裂既有检查点数据。
    """

    assert build_thread_id("user-001", "conv-1") == '["user-001","conv-1"]'


def public_state_snapshot(saver, config):
    """独立的参考实现:经真实聊天图公开构建入口(get_state)。

    测试端自己构造参考(不调用被测的读取图工厂),用于钉死
    “待应用写入必须可见”这一 LangGraph 语义;历史接口必须与之一致。
    读取只重算未处理任务,不会调用模型(bind_tools 之外无任何模型调用)。
    """

    graph = create_tool_call_graph(
        model=ScriptedModel([]),
        registry=create_tool_registry(),
        checkpointer=saver,
    )

    return graph.get_state(config)


def test_history_reads_two_turns_after_close_and_reopen(
    checkpoint_db,
    client,
):
    """两轮真实对话完成后(连接关闭再重开),历史接口应读到全部 4 条,
    且全程没有调用任何模型/工具/记忆工厂(fixture 已打桩为失败)。"""

    conversation_id = create_session(client)

    seed_conversation(
        checkpoint_db,
        "user-001",
        conversation_id,
        completed_two_turns(),
    )

    response = client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200

    assert response.json() == [
        {"role": "user", "content": "我的第一问", "attachments": []},
        {"role": "assistant", "content": "第一答。", "attachments": []},
        {"role": "user", "content": "我的第二问", "attachments": []},
        {"role": "assistant", "content": "第二答。", "attachments": []},
    ]


def test_history_endpoint_matches_public_state_read_with_pending_writes(
    checkpoint_db,
    client,
):
    """有效的待应用写入必须在历史接口中可见,与 graph.get_state 一致。

    复现项目经理的对照:原实现直接读最新检查点的 channel_values 时
    看不到待应用写入;公开状态读取(graph.get_state)能看到。

    待应用写入只有在“存在未处理任务”的检查点上才会被 get_state 应用,
    且应用与否按读取图拓扑重算任务 —— 夹具用真实 agent 图跑第三轮
    (模型返回 tool_calls),在 tools 节点前中断,得到崩溃/中断在超步
    之间同样的挂起态;随后用公开 put_writes 向该未处理任务写入一条
    助手消息。

    该挂起态下 channel_values 已有 6 条(含空 content 的 tool_calls
    助手消息,会被接口过滤为 5 条可见);待应用写入生效后参考读取为
    7 条、接口可见 6 条。修复前接口只返回 5 条(看不到待应用写入)。
    """

    conversation_id = create_session(client)

    seed_conversation(
        checkpoint_db,
        "user-001",
        conversation_id,
        completed_two_turns(),
    )

    config = thread_config("user-001", conversation_id)

    pending_message = AIMessage(content="尚未并入检查点的待应用消息")

    with SqliteSaver.from_conn_string(checkpoint_db) as saver:
        saver.setup()

        # 真实 agent 图跑第三轮,在 tools 节点前中断:
        # 检查点存在未处理任务,且模型不会被再次调用。
        paused = create_tool_call_graph(
            model=ScriptedModel(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "text_length",
                                "args": {"text": "hello"},
                                "id": "call_pending",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            ),
            registry=create_tool_registry(),
            checkpointer=saver,
            interrupt_before=["tools"],
        )

        paused.invoke(
            {"messages": [HumanMessage(content="用工具数一次数")]},
            config,
        )

        paused_state = paused.get_state(config)

        # 线程处于挂起态:未处理任务是 tools(公开 API 断言)。
        assert paused_state.next == ("tools",)

        before = saver.get_tuple(config)

        # 2 轮已完成(4) + 第三轮 human + tool_calls 助手消息(空 content)。
        assert len(before.checkpoint["channel_values"]["messages"]) == 6
        assert not before.pending_writes

        # 向未处理任务写入(公开 API,不手写内部合并协议)。
        pending_task_id = paused_state.tasks[0].id

        saver.put_writes(
            before.config,
            [("messages", [pending_message])],
            task_id=pending_task_id,
        )

        # 公开状态读取(与聊天路径同一语义)应看到 7 条。
        snapshot = public_state_snapshot(saver, config)

        assert len(snapshot.values["messages"]) == 7

        # 原始 channel_values 仍然只有 6 条:差异必须体现在接口输出上。
        after = saver.get_tuple(config)

        assert len(after.checkpoint["channel_values"]["messages"]) == 6

    response = client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200

    messages = response.json()

    # 可见消息 = 4 条已完成 + 第三轮 user + 待应用写入的助手消息;
    # 空 content 的 tool_calls 助手消息被过滤。修复前:只有 5 条。
    assert len(messages) == 6

    assert messages[4] == {
        "role": "user",
        "content": "用工具数一次数",
        "attachments": [],
    }

    assert messages[-1] == {
        "role": "assistant",
        "content": "尚未并入检查点的待应用消息",
        "attachments": [],
    }


def test_history_filters_tool_and_empty_messages_keeps_json_blocks(
    checkpoint_db,
    client,
):
    """真实消息流经过滤规则:tool 消息与空 content 被过滤,
    list content 走 JSON 兼容分支。"""

    conversation_id = create_session(client)

    block_content = [{"type": "text", "text": "字符数:5"}]

    seed_conversation(
        checkpoint_db,
        "user-001",
        conversation_id,
        [
            ("数字数", [AIMessage(content="第一答。")]),
            (
                "用工具数字数",
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "text_length",
                                "args": {"text": "hello"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content=block_content),
                ],
            ),
            ("再答一次就好", [AIMessage(content="")]),
        ],
    )

    response = client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200

    messages = response.json()

    # 期望:3 条 user + “第一答。” + JSON 化的 list content;
    # tool_calls-only 的空 content ai、ToolMessage、空 content ai 均被过滤。
    assert messages[0] == {
        "role": "user",
        "content": "数字数",
        "attachments": [],
    }
    assert messages[1] == {
        "role": "assistant",
        "content": "第一答。",
        "attachments": [],
    }
    assert messages[2] == {
        "role": "user",
        "content": "用工具数字数",
        "attachments": [],
    }
    assert messages[3] == {
        "role": "assistant",
        "content": json.dumps(block_content, ensure_ascii=False),
        "attachments": [],
    }
    assert messages[4] == {
        "role": "user",
        "content": "再答一次就好",
        "attachments": [],
    }
    assert len(messages) == 5


def test_attachments_metadata_persist_note_not_persisted(
    checkpoint_db,
    client,
):
    """附件端到端(真实 SQLite):图片块+语音元数据经 build_user_message
    写入检查点并可读回;语音防呆提示只进模型调用副本,不落 state。

    钉死三件事:additional_kwargs 经检查点序列化往返存活;历史正文取
    文本块拼接(不回传图片 base64);附件元数据完整返回。
    """

    conversation_id = create_session(client)

    user_message = build_user_message(
        ChatRequest(
            message="看看图和录音",
            conversation_id=conversation_id,
            attachments=[
                Attachment(
                    kind="image",
                    name="shot.png",
                    mime="image/png",
                    # M1-a 起 data 必须是真实图片字节(魔数嗅探)。
                    size=8,
                    data="iVBORw0KGgo=",
                ),
                Attachment(
                    kind="audio",
                    name="voice.wav",
                    mime="audio/wav",
                    size=1024,
                ),
            ],
        ),
        "user-001",
        conversation_id,
    )

    model = ScriptedModel([AIMessage(content="图看懂了;语音读不了。")])

    with SqliteSaver.from_conn_string(checkpoint_db) as saver:
        saver.setup()

        graph = create_tool_call_graph(
            model=model,
            registry=create_tool_registry(),
            checkpointer=saver,
        )

        graph.invoke(
            {"messages": [user_message]},
            thread_config("user-001", conversation_id),
        )

        call_copy = model.seen[0]

        notes = [
            message
            for message in call_copy
            if isinstance(message, SystemMessage) and "无法读取" in str(message.content)
        ]

        assert len(notes) == 1
        assert "voice.wav" in notes[0].content
        assert "shot.png" not in notes[0].content

        raw = saver.get_tuple(thread_config("user-001", conversation_id))

        # 防呆提示不落 state:原始通道消息只有 human + ai 两条。
        assert len(raw.checkpoint["channel_values"]["messages"]) == 2

    response = client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200

    assert response.json() == [
        {
            "role": "user",
            "content": "看看图和录音",
            "attachments": [
                {
                    "kind": "image",
                    "name": "shot.png",
                    "mime": "image/png",
                    "size": 8,
                    # M1-b/M1-c 新增字段:无转译状态/未持久化 blob 时恒为 null
                    # (图片无转译;此条语音无 data,故也无 audio_id)。
                    "extract_status": None,
                    "audio_id": None,
                },
                {
                    "kind": "audio",
                    "name": "voice.wav",
                    "mime": "audio/wav",
                    "size": 1024,
                    "extract_status": None,
                    "audio_id": None,
                },
            ],
        },
        {
            "role": "assistant",
            "content": "图看懂了;语音读不了。",
            "attachments": [],
        },
    ]


def test_history_without_thread_returns_empty(checkpoint_db, client):
    conversation_id = create_session(client)

    response = client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_history_user_isolation_on_real_store(checkpoint_db, client):
    """真实库上的用户隔离:线程 ID 含 user_id,
    跨用户访问既拿不到内容,也在所有权检查处被拒。"""

    conversation_a = create_session(client, api_key="test-token")

    seed_conversation(
        checkpoint_db,
        "user-001",
        conversation_a,
        completed_two_turns(),
    )

    conversation_b = create_session(client, api_key="other-token")

    # 另一用户自己的空线程:正常返回空,不泄露他人内容。
    own = client.get(
        f"/sessions/{conversation_b}/messages",
        headers={"X-API-Key": "other-token"},
    )

    assert own.status_code == 200
    assert own.json() == []

    # 另一用户没有该会话行:所有权检查先于任何状态读取,404。
    cross = client.get(
        f"/sessions/{conversation_a}/messages",
        headers={"X-API-Key": "other-token"},
    )

    assert cross.status_code == 404

    # 所有者仍读得到自己的完整历史(线程编码 [user_id, conversation_id])。
    owner = client.get(
        f"/sessions/{conversation_a}/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert owner.status_code == 200
    assert len(owner.json()) == 4
