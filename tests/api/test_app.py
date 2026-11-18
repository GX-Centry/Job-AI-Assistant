import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk, HumanMessage

from job_agent.api import app as api_module
from job_agent.api.app import (
    ChatRequest,
    ChatResponse,
    create_app,
    run_chat,
)


def fake_handler(request, user_id):
    return ChatResponse(
        conversation_id=request.conversation_id,
        answer=f"Echo: {request.message}",
    )


class FakeCheckpointer:
    def __init__(self):
        self.deleted_threads = []

    def setup(self):
        pass

    def delete_thread(self, thread_id):
        self.deleted_threads.append(thread_id)


def exploding(name: str):
    def factory(*args, **kwargs):
        raise AssertionError(f"{name} must not be called")

    return factory


def patch_graph_dependencies(
    monkeypatch,
    graph,
    checkpointer=None,
):
    if checkpointer is None:
        checkpointer = FakeCheckpointer()

    monkeypatch.setattr(
        api_module.SqliteSaver,
        "from_conn_string",
        lambda path: nullcontext(checkpointer),
    )

    monkeypatch.setattr(
        api_module,
        "open_memory_store",
        lambda path: nullcontext(object()),
    )

    monkeypatch.setattr(
        api_module,
        "create_chat_model",
        lambda: object(),
    )

    monkeypatch.setattr(
        api_module,
        "create_tool_registry",
        lambda: object(),
    )

    monkeypatch.setattr(
        api_module,
        "create_tool_call_graph",
        lambda **kwargs: graph,
    )

    return checkpointer


@pytest.fixture(autouse=True)
def configure_api_keys(monkeypatch):
    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        '{"test-token":"user-001"}',
    )


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )


@pytest.fixture
def client():
    return TestClient(create_app(fake_handler))


def create_test_session(client, title="Test conversation"):
    response = client.post(
        "/sessions",
        json={"title": title},
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 201

    return response.json()["conversation_id"]


# ==================================================
# Health Check
# ==================================================


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_without_api_keys_or_model_dependencies(monkeypatch):
    """健康检查与模型解耦:未配置 API Key 与任何模型/存储工厂时仍可用
    (模型未配置时,健康检查与不依赖模型的会话/历史必须可独立验证)。"""

    monkeypatch.delenv("JOB_AGENT_API_KEYS", raising=False)

    for name in (
        "open_memory_store",
        "create_chat_model",
        "create_tool_registry",
        "create_tool_call_graph",
    ):
        monkeypatch.setattr(api_module, name, exploding(name))

    test_client = TestClient(create_app(fake_handler))

    response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # 会话/历史同样不依赖模型:未配置密钥时未认证请求是 401/503,
    # 而不是模型工厂崩溃 —— 工厂打桩为失败,未被触碰即证明解耦。
    sessions = test_client.get("/sessions")

    assert sessions.status_code in (401, 403, 503)


# ==================================================
# Chat
# ==================================================


def test_chat(client, isolated_db):
    conversation_id = create_test_session(client)

    response = client.post(
        "/chat",
        json={
            "message": "hello",
            "conversation_id": conversation_id,
        },
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "Echo: hello"


def test_empty_message_rejected(client):
    response = client.post(
        "/chat",
        json={
            "message": "   ",
            "conversation_id": "test-001",
        },
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 422


def test_chat_requires_conversation_id(client):
    response = client.post(
        "/chat",
        json={"message": "hello"},
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 422


def test_chat_requires_api_key(client):
    response = client.post(
        "/chat",
        json={
            "message": "hello",
            "conversation_id": "test-001",
        },
    )

    assert response.status_code == 401


def test_invalid_api_key_rejected(client):
    response = client.post(
        "/chat",
        json={
            "message": "hello",
            "conversation_id": "test-001",
        },
        headers={"X-API-Key": "wrong-token"},
    )

    assert response.status_code == 401


def test_chat_rejects_other_users_session(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        '{"test-token":"user-001","other-token":"user-002"}',
    )

    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    test_client = TestClient(create_app(fake_handler))

    conversation_id = create_test_session(test_client)

    response = test_client.post(
        "/chat",
        json={
            "message": "hello",
            "conversation_id": conversation_id,
        },
        headers={"X-API-Key": "other-token"},
    )

    assert response.status_code == 404


# ==================================================
# Run Chat
# ==================================================


def test_run_chat(monkeypatch):
    class FakeGraph:
        def invoke(self, state, config):
            assert state["messages"][0].content == "hello"

            assert config["configurable"]["user_id"] == "user-001"

            assert config["configurable"]["thread_id"] == api_module.build_thread_id(
                "user-001",
                "test-001",
            )

            return {"messages": [SimpleNamespace(content="Hello from Agent")]}

    patch_graph_dependencies(
        monkeypatch,
        FakeGraph(),
    )

    result = run_chat(
        ChatRequest(
            message="hello",
            conversation_id="test-001",
        ),
        user_id="user-001",
    )

    assert result.answer == "Hello from Agent"
    assert result.conversation_id == "test-001"


def test_run_chat_rejects_empty_messages(monkeypatch):
    class FakeGraph:
        def invoke(self, state, config):
            return {"messages": []}

    patch_graph_dependencies(
        monkeypatch,
        FakeGraph(),
    )

    with pytest.raises(RuntimeError):
        run_chat(
            ChatRequest(
                message="hello",
                conversation_id="test-001",
            ),
            user_id="user-001",
        )


def test_run_chat_rejects_non_string_answer(monkeypatch):
    class FakeGraph:
        def invoke(self, state, config):
            return {"messages": [SimpleNamespace(content={"answer": "hello"})]}

    patch_graph_dependencies(
        monkeypatch,
        FakeGraph(),
    )

    with pytest.raises(TypeError):
        run_chat(
            ChatRequest(
                message="hello",
                conversation_id="test-001",
            ),
            user_id="user-001",
        )


# ==================================================
# Create Session
# ==================================================


def test_create_session_generates_conversation_id(
    client,
    isolated_db,
):
    response = client.post(
        "/sessions",
        json={},
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 201
    assert response.json()["conversation_id"]
    assert response.json()["title"] == "New conversation"


def test_create_session_rejects_blank_title(
    client,
    isolated_db,
):
    response = client.post(
        "/sessions",
        json={"title": "   "},
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 422


# ==================================================
# List Sessions
# ==================================================


def test_list_sessions(client, isolated_db):
    first_id = create_test_session(
        client,
        "First conversation",
    )

    second_id = create_test_session(
        client,
        "Second conversation",
    )

    response = client.get(
        "/sessions",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200

    sessions = response.json()

    assert len(sessions) == 2

    session_ids = {session["conversation_id"] for session in sessions}

    assert first_id in session_ids
    assert second_id in session_ids


def test_list_sessions_user_isolation(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        '{"test-token":"user-001","other-token":"user-002"}',
    )

    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    test_client = TestClient(create_app(fake_handler))

    create_test_session(test_client)

    response = test_client.get(
        "/sessions",
        headers={"X-API-Key": "other-token"},
    )

    assert response.status_code == 200
    assert response.json() == []


# ==================================================
# Get Conversation Messages
# ==================================================


# 历史内容读取的正确性(过滤规则、空历史、线程编码、用户隔离、
# 待应用写入可见性)由 tests/api/test_history_read.py 用真实临时
# SQLite + 真实 LangGraph 状态读取钉死;此处只保留 API 层守卫。


def test_get_messages_rejects_other_users_without_model_dependencies(
    monkeypatch,
    tmp_path,
):
    """跨用户访问:所有权检查必须先于任何存储打开与状态读取。

    把检查点存储的打开本身打桩为“被调用即失败”:404 必须在
    from_conn_string 之前达成;模型/工具/记忆工厂同样不可触碰。
    """

    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        '{"test-token":"user-001","other-token":"user-002"}',
    )

    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    monkeypatch.setattr(
        api_module.SqliteSaver,
        "from_conn_string",
        exploding("SqliteSaver.from_conn_string"),
    )

    monkeypatch.setattr(
        api_module,
        "open_memory_store",
        exploding("open_memory_store"),
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
        "create_tool_call_graph",
        exploding("create_tool_call_graph"),
    )

    test_client = TestClient(create_app(fake_handler))

    conversation_id = create_test_session(test_client)

    response = test_client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "other-token"},
    )

    assert response.status_code == 404


def test_get_messages_rejects_unknown_session(
    client,
    isolated_db,
):
    response = client.get(
        "/sessions/unknown-session/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 404


def test_get_messages_rejects_other_users(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        '{"test-token":"user-001","other-token":"user-002"}',
    )

    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    test_client = TestClient(create_app(fake_handler))

    conversation_id = create_test_session(test_client)

    response = test_client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "other-token"},
    )

    assert response.status_code == 404


# ==================================================
# Delete Session
# ==================================================


def test_delete_session(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    test_client = TestClient(create_app(fake_handler))

    conversation_id = create_test_session(test_client)

    checkpointer = FakeCheckpointer()

    monkeypatch.setattr(
        api_module.SqliteSaver,
        "from_conn_string",
        lambda path: nullcontext(checkpointer),
    )

    response = test_client.delete(
        f"/sessions/{conversation_id}",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 204

    expected_thread_id = api_module.build_thread_id(
        "user-001",
        conversation_id,
    )

    assert checkpointer.deleted_threads == [expected_thread_id]

    # Confirm session metadata was deleted.
    list_response = test_client.get(
        "/sessions",
        headers={"X-API-Key": "test-token"},
    )

    assert list_response.status_code == 200
    assert list_response.json() == []


def test_delete_unknown_session(
    client,
    isolated_db,
):
    response = client.delete(
        "/sessions/unknown-session",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 404


def test_delete_session_rejects_other_users(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        '{"test-token":"user-001","other-token":"user-002"}',
    )

    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    test_client = TestClient(create_app(fake_handler))

    conversation_id = create_test_session(test_client)

    response = test_client.delete(
        f"/sessions/{conversation_id}",
        headers={"X-API-Key": "other-token"},
    )

    assert response.status_code == 404

    # Original owner's session must still exist.
    list_response = test_client.get(
        "/sessions",
        headers={"X-API-Key": "test-token"},
    )

    assert len(list_response.json()) == 1


def test_chat_stream(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    test_client = TestClient(create_app(fake_handler))

    conversation_id = create_test_session(test_client)

    def fake_stream(request, user_id):
        yield api_module.sse_event(
            "token",
            {"content": "Hello"},
        )
        yield api_module.sse_event(
            "token",
            {"content": " World"},
        )
        yield api_module.sse_event(
            "done",
            {"conversation_id": conversation_id},
        )

    monkeypatch.setattr(
        api_module,
        "stream_chat",
        fake_stream,
    )

    response = test_client.post(
        "/chat/stream",
        json={
            "message": "hello",
            "conversation_id": conversation_id,
        },
        headers={
            "X-API-Key": "test-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    assert "Hello" in response.text
    assert " World" in response.text
    assert "event: done" in response.text


# ==================================================
# Chat Stream authentication
# ==================================================


def test_chat_stream_requires_api_key(client, isolated_db):
    conversation_id = create_test_session(client)

    response = client.post(
        "/chat/stream",
        json={
            "message": "hello",
            "conversation_id": conversation_id,
        },
    )

    assert response.status_code == 401


def test_chat_stream_rejects_invalid_api_key(client, isolated_db):
    conversation_id = create_test_session(client)

    response = client.post(
        "/chat/stream",
        json={
            "message": "hello",
            "conversation_id": conversation_id,
        },
        headers={"X-API-Key": "wrong-token"},
    )

    assert response.status_code == 401


def test_chat_stream_rejects_other_users_session(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "JOB_AGENT_API_KEYS",
        '{"test-token":"user-001","other-token":"user-002"}',
    )

    monkeypatch.setattr(
        api_module.session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )

    test_client = TestClient(create_app(fake_handler))

    conversation_id = create_test_session(test_client)

    response = test_client.post(
        "/chat/stream",
        json={
            "message": "hello",
            "conversation_id": conversation_id,
        },
        headers={"X-API-Key": "other-token"},
    )

    assert response.status_code == 404


# ==================================================
# Real stream_chat hardening
# 只替换外部依赖(SqliteSaver / store / 模型 / 工具 / 图),
# 被测对象仍是 job_agent.api.app.stream_chat 生成器本体。
# ==================================================


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []

    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue

        name = None
        data = None

        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])

        events.append((name, data))

    return events


class TrackedContext:
    """记录 with 上下文的进入/退出次数,用于资源释放断言。"""

    def __init__(self, inner):
        self.inner = inner
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self):
        self.enter_count += 1

        return self.inner

    def __exit__(self, exc_type, exc, tb):
        self.exit_count += 1

        return False


class FakeStreamGraph:
    def __init__(self, chunks=None, stream_error=None):
        self.chunks = chunks if chunks is not None else []
        self.stream_error = stream_error
        self.stream_calls = []
        self.stream_closed = False

    def stream(self, state, config, *, stream_mode=None):
        self.stream_calls.append((state, config, stream_mode))

        try:
            yield from self.chunks

            if self.stream_error is not None:
                raise self.stream_error
        finally:
            self.stream_closed = True


def patch_stream_dependencies(monkeypatch, graph):
    checkpointer = FakeCheckpointer()
    saver_context = TrackedContext(checkpointer)
    store_context = TrackedContext(object())

    monkeypatch.setattr(
        api_module.SqliteSaver,
        "from_conn_string",
        lambda path: saver_context,
    )

    monkeypatch.setattr(
        api_module,
        "open_memory_store",
        lambda path: store_context,
    )

    monkeypatch.setattr(
        api_module,
        "create_chat_model",
        lambda: object(),
    )

    monkeypatch.setattr(
        api_module,
        "create_tool_registry",
        lambda: object(),
    )

    monkeypatch.setattr(
        api_module,
        "create_tool_call_graph",
        lambda **kwargs: graph,
    )

    activity_calls = []

    monkeypatch.setattr(
        api_module.session_store,
        "update_session_activity",
        lambda user_id, conversation_id: activity_calls.append(
            (user_id, conversation_id)
        ),
    )

    return saver_context, store_context, activity_calls


def make_stream_request() -> ChatRequest:
    return ChatRequest(
        message="你好",
        conversation_id="stream-001",
    )


def test_stream_chat_emits_tokens_and_done(monkeypatch):
    graph = FakeStreamGraph(
        chunks=[
            (AIMessageChunk(content="你"), {"langgraph_node": "agent"}),
            (SimpleNamespace(content="not-an-ai-chunk"), {}),
            (
                AIMessageChunk(content=[{"type": "text", "text": "好"}]),
                {},
            ),
            (AIMessageChunk(content=""), {}),
            (AIMessageChunk(content="！"), {}),
        ],
    )

    saver_context, store_context, activity_calls = patch_stream_dependencies(
        monkeypatch,
        graph,
    )

    events = parse_sse(
        "".join(
            api_module.stream_chat(
                make_stream_request(),
                "user-001",
            )
        )
    )

    assert [(name, data) for name, data in events if name == "token"] == [
        ("token", {"content": "你"}),
        ("token", {"content": "好"}),
        ("token", {"content": "！"}),
    ]

    assert events[-1] == ("done", {"conversation_id": "stream-001"})

    state, config, stream_mode = graph.stream_calls[0]

    assert stream_mode == "messages"
    assert isinstance(state["messages"][0], HumanMessage)
    assert config["configurable"]["thread_id"] == api_module.build_thread_id(
        "user-001",
        "stream-001",
    )
    assert config["configurable"]["user_id"] == "user-001"

    assert activity_calls == [("user-001", "stream-001")]
    assert saver_context.enter_count == 1
    assert saver_context.exit_count == 1
    assert store_context.exit_count == 1
    assert graph.stream_closed is True


def test_stream_chat_reports_model_init_failure(monkeypatch):
    graph = FakeStreamGraph()

    saver_context, store_context, activity_calls = patch_stream_dependencies(
        monkeypatch,
        graph,
    )

    monkeypatch.setattr(
        api_module,
        "create_chat_model",
        exploding("create_chat_model"),
    )

    events = parse_sse(
        "".join(
            api_module.stream_chat(
                make_stream_request(),
                "user-001",
            )
        )
    )

    assert events == [("error", {"message": "Agent execution failed."})]
    assert graph.stream_calls == []
    assert activity_calls == []

    # 初始化失败同样必须释放已打开的持久化上下文。
    assert saver_context.exit_count == 1
    assert store_context.exit_count == 1


def test_stream_chat_reports_mid_stream_failure(monkeypatch):
    graph = FakeStreamGraph(
        chunks=[(AIMessageChunk(content="partial"), {})],
        stream_error=RuntimeError("model connection dropped"),
    )

    saver_context, store_context, activity_calls = patch_stream_dependencies(
        monkeypatch,
        graph,
    )

    events = parse_sse(
        "".join(
            api_module.stream_chat(
                make_stream_request(),
                "user-001",
            )
        )
    )

    assert events[0] == ("token", {"content": "partial"})
    assert events[1] == ("error", {"message": "Agent execution failed."})
    assert activity_calls == []
    assert saver_context.exit_count == 1
    assert store_context.exit_count == 1
    assert graph.stream_closed is True


def test_stream_chat_releases_resources_when_generator_closed(monkeypatch):
    graph = FakeStreamGraph(
        chunks=[
            (AIMessageChunk(content="first"), {}),
            (AIMessageChunk(content="second"), {}),
        ],
    )

    saver_context, store_context, activity_calls = patch_stream_dependencies(
        monkeypatch,
        graph,
    )

    stream = api_module.stream_chat(
        make_stream_request(),
        "user-001",
    )

    first_event = next(stream)

    assert first_event == api_module.sse_event(
        "token",
        {"content": "first"},
    )

    # 模拟客户端断连:关闭生成器后,所有持久化上下文必须退出,
    # 且不得走到“会话活动已更新”的收尾逻辑。
    stream.close()

    assert graph.stream_closed is True
    assert saver_context.enter_count == 1
    assert saver_context.exit_count == 1
    assert store_context.exit_count == 1
    assert activity_calls == []
