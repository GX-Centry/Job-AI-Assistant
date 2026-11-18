import base64
import binascii
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Response,
)
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage

logger = logging.getLogger(__name__)

from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field, field_validator, model_validator

from job_agent.api import audio_store, session_store
from job_agent.api.attachment_parsers import (
    MAX_EXTRACTED_CHARS,
    ParseFailure,
    parse_archive,
    parse_attachment,
)
from job_agent.api.attachment_types import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    PARSEABLE_RESOLVED,
    ROUTE_POLICIES,
    channel_of,
    sniff_resolved,
)
from job_agent.api.security import get_current_user_id
from job_agent.graph import create_history_read_graph, create_tool_call_graph
from job_agent.llm.factory import create_chat_model
from job_agent.memory.store import open_memory_store
from job_agent.tools.factory import create_tool_registry

CHECKPOINT_DB = "data/agent_checkpoints.sqlite"
MEMORY_DB = "data/agent_memory.sqlite"

# ==================================================
# Data Models
# ==================================================


# 附件契约(M1-a 识别层 + M1-b 文档/文本 + M1-c 语音/zip):
# - kind=image:唯一可被当前模型直接读取的类型,必带 base64 data,
#   解码后单图 ≤ MAX_IMAGE_BYTES;**魔数嗅探强校验**(attachment_types.py,
#   内容字节必须是真实图片,伪装的假图片一律 422 拒绝);
# - kind=file(**M1-b 起可带 data**):data 仅当魔数命中 PARSEABLE_RESOLVED
#   (pdf/docx/xlsx/pptx/text/**zip**)时被接收,限额按路由策略表(document
#   20MiB、text 1MiB、archive 20MiB);请求时就地转译为带来源标记的文本块
#   (zip 走内存解包重路由,护栏见 parse_archive;解析即用,字节不持久)。
#   无 data 或不可解析内容(图片/音频伪装进 file/ole2/unknown)仍只登记元数据;
# - kind=audio(**M1-c 起可带 data**):必带 audio/* mime;data 按魔数强校验
#   必须是真实音频族(伪装 422),解码 ≤ MAX_AUDIO_BYTES 且 size 相符;字节
#   持久化在 audio_store(**挂会话生命周期**,删会话即删),audio_id 进元数据
#   与历史;转译**不在请求期发生** —— 实测网关静默丢弃 input_audio,故由模型
#   按需调用 transcribe_audio 工具(服务端本机 faster-whisper)取回文本;
# - 数量 ≤ MAX_ATTACHMENTS。
AttachmentKind = Literal["image", "audio", "file"]

MAX_ATTACHMENTS = 3


class Attachment(BaseModel):
    kind: AttachmentKind
    name: str = Field(min_length=1, max_length=200)
    mime: str = Field(min_length=1, max_length=120)
    size: int = Field(ge=0)
    data: str | None = None

    @field_validator("name", "mime")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field cannot be empty.")

        return value.strip()

    @model_validator(mode="after")
    def validate_kind_contract(self) -> "Attachment":
        if self.kind == "image":
            if not self.mime.startswith("image/"):
                raise ValueError("image attachment requires an image/* mime.")

            if not self.data:
                raise ValueError("image attachment requires base64 data.")

            try:
                payload = base64.b64decode(self.data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(
                    "image attachment data must be valid base64.",
                ) from exc

            if len(payload) > MAX_IMAGE_BYTES:
                raise ValueError(
                    f"image attachment exceeds {MAX_IMAGE_BYTES} bytes.",
                )

            if self.size != len(payload):
                raise ValueError(
                    "image attachment size must match decoded data size.",
                )

            # 嗅探仲裁:内容字节是权威信号。声明 image 但魔数不是图片
            # (webm/文本/PDF 伪装等)一律拒绝 —— 喂给网关只会报错或
            # 诱导模型对不存在的图片瞎编。
            if channel_of(sniff_resolved(payload)) != "multimodal":
                raise ValueError(
                    "image attachment content does not match an image "
                    "(magic bytes mismatch).",
                )
        elif self.kind == "audio":
            if not self.mime.startswith("audio/"):
                raise ValueError("audio attachment requires an audio/* mime.")

            if self.data is not None:
                # M1-c:ASR 消费方已就位,audio data 被接收 —— 但内容字节是
                # 权威,必须是真实音频族(伪装一律 422;空 data 落 unknown)。
                try:
                    payload = base64.b64decode(self.data, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError(
                        "audio attachment data must be valid base64.",
                    ) from exc

                if channel_of(sniff_resolved(payload)) != "asr":
                    raise ValueError(
                        "audio attachment data must be genuine audio content "
                        "(magic bytes mismatch).",
                    )

                if len(payload) > MAX_AUDIO_BYTES:
                    raise ValueError(
                        f"audio attachment exceeds {MAX_AUDIO_BYTES} bytes.",
                    )

                if self.size != len(payload):
                    raise ValueError(
                        "audio attachment size must match decoded data size.",
                    )
        elif self.data is not None:
            # file + data(M1-b):内容字节是权威 —— 只有嗅探为已接线可解析
            # 通道才收(data 无消费方不预收,其余一律回落"无 data 仅元数据")。
            try:
                payload = base64.b64decode(self.data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(
                    "file attachment data must be valid base64.",
                ) from exc

            resolved = sniff_resolved(payload)

            if resolved not in PARSEABLE_RESOLVED:
                raise ValueError(
                    "file attachment data requires parseable content "
                    "(pdf/docx/xlsx/pptx/plain text/zip, judged by magic bytes).",
                )

            limit = ROUTE_POLICIES[channel_of(resolved)].max_bytes

            if len(payload) > limit:
                raise ValueError(
                    f"file attachment exceeds {limit} bytes.",
                )

            if self.size != len(payload):
                raise ValueError(
                    "file attachment size must match decoded data size.",
                )

        return self


class ChatRequest(BaseModel):
    message: str = Field(
        # 纯附件消息允许空文本(占位符由消息构造填充)。
        min_length=0,
        max_length=10000,
    )

    conversation_id: str = Field(
        min_length=1,
        max_length=128,
    )

    attachments: list[Attachment] = Field(
        default_factory=list,
        max_length=MAX_ATTACHMENTS,
    )

    @field_validator("conversation_id")
    @classmethod
    def reject_blank_conversation_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field cannot be empty.")

        return value.strip()

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        # 允许空文本(纯附件消息),其余仅去空白;
        # “文本与附件至少有一项”由模型级校验保证。
        return value.strip()

    @model_validator(mode="after")
    def require_text_or_attachments(self) -> "ChatRequest":
        if not self.message and not self.attachments:
            raise ValueError("message or attachments are required.")

        return self


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str


class AttachmentInfo(BaseModel):
    kind: AttachmentKind
    name: str
    mime: str
    size: int
    # M1-b:文档/文本附件的就地转译结果(仅 file+data 时有值);
    # 旧检查点数据与 audio/metadata-only file 均为 None。
    extract_status: Literal["parsed", "failed"] | None = None
    # M1-c:已持久化语音 blob 的引用(audio_store 主键),供历史端点回读
    # 与 transcribe_audio 工具定位;旧数据与未持久化的语音均为 None。
    audio_id: str | None = None


class SessionCreateRequest(BaseModel):
    title: str = Field(
        default="New conversation",
        min_length=1,
        max_length=120,
    )

    @field_validator("title")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title cannot be empty.")

        return value.strip()


class SessionResponse(BaseModel):
    conversation_id: str
    title: str
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    attachments: list[AttachmentInfo] = Field(default_factory=list)


# ==================================================
# Utilities
# ==================================================


def ensure_data_dir() -> None:
    # 目录随 CHECKPOINT_DB 推导:打桩/重定向存储路径后不再创建无关目录。
    Path(CHECKPOINT_DB).parent.mkdir(
        parents=True,
        exist_ok=True,
    )


def build_thread_id(
    user_id: str,
    conversation_id: str,
) -> str:
    return json.dumps(
        [user_id, conversation_id],
        separators=(",", ":"),
    )


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_user_message(
    request: ChatRequest,
    user_id: str,
    conversation_id: str,
) -> HumanMessage:
    # 图片作为 image_url content block 进入模型(实测当前网关唯一可读的
    # 多模态通道);文档/文本/zip 附件(M1-b/M1-c)在请求时**就地转译**为带
    # 来源标记的文本 content block(策略 A:转译文本随消息进检查点,字节不
    # 持久);语音附件(M1-c)字节持久化进 audio_store,audio_id 进元数据,
    # **内容不进模型** —— 网关会静默丢弃 input_audio,转写由模型按需调用
    # transcribe_audio 工具完成;不可解析文件仍只登记元数据。防呆提示由图
    # 节点注入(已转译/可转写的附件按状态区分,见 graph.py)。
    if not request.attachments:
        return HumanMessage(content=request.message)

    blocks: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []

    for attachment in request.attachments:
        entry = attachment.model_dump(
            mode="json",
            include={"kind", "name", "mime", "size"},
        )

        if attachment.kind == "image":
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{attachment.mime};base64,{attachment.data}",
                    },
                },
            )
        elif attachment.kind == "audio" and attachment.data:
            # M1-c:校验层已保证是真实音频族字节;存 blob 拿 audio_id。
            # 存储失败不吞消息 —— 降级为纯元数据(与旧契约一致),显式记日志。
            try:
                payload = base64.b64decode(attachment.data, validate=True)
                record = audio_store.store_audio(
                    user_id,
                    conversation_id,
                    attachment.name,
                    attachment.mime,
                    sniff_resolved(payload),
                    payload,
                )
            except (ParseFailure, ValueError, binascii.Error) as exc:
                logger.warning(
                    "audio store rejected payload: name=%s mime=%s err=%s",
                    attachment.name,
                    attachment.mime,
                    exc,
                )
            except Exception:
                logger.exception(
                    "audio store failed: name=%s mime=%s",
                    attachment.name,
                    attachment.mime,
                )
            else:
                entry["audio_id"] = record["audio_id"]
        elif attachment.kind == "file" and attachment.data:
            # 校验层已保证魔数可解析;这里失败只可能来自"能嗅探但转译失败"
            # (如空文档/无可转译成员),显式失败态,绝不静默塞假文本。
            entry["extract_status"] = "failed"

            try:
                payload = base64.b64decode(attachment.data, validate=True)
                resolved = sniff_resolved(payload)

                if resolved == "zip":
                    outcome = parse_archive(payload)
                    marker = (
                        f"[解包自 {attachment.name},"
                        "仅转译 pdf/docx/xlsx/pptx/text 族成员(内存解包,不落盘)]"
                    )
                else:
                    outcome = parse_attachment(payload, resolved)
                    marker = f"[文档解析自 {attachment.name},转译文本可能有格式损失]"
            except (ParseFailure, ValueError, binascii.Error):
                logger.warning(
                    "attachment parse failed: name=%s mime=%s",
                    attachment.name,
                    attachment.mime,
                )
            else:
                entry["extract_status"] = "parsed"

                if outcome.truncated:
                    marker += f"(已截断,仅保留前 {MAX_EXTRACTED_CHARS} 字符)"

                skipped = getattr(outcome, "members_skipped", None)

                if skipped:
                    marker += f";未转译条目 {len(skipped)} 个:{'、'.join(skipped[:10])}"

                    if len(skipped) > 10:
                        marker += f" 等共 {len(skipped)} 个"

                blocks.append({"type": "text", "text": f"{marker}\n{outcome.text}"})

        metadata.append(entry)

    if request.message:
        blocks.insert(0, {"type": "text", "text": request.message})

    if not blocks:
        blocks.append({"type": "text", "text": "(无文字说明的附件消息)"})

    return HumanMessage(
        content=blocks,
        additional_kwargs={"attachments": metadata},
    )


def extract_chunk_text(content) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )

    return ""


# ==================================================
# Agent Execution
# ==================================================


def run_chat(
    request: ChatRequest,
    user_id: str,
) -> ChatResponse:

    ensure_data_dir()

    with SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        checkpointer.setup()

        with open_memory_store(MEMORY_DB) as store:
            graph = create_tool_call_graph(
                model=create_chat_model(),
                registry=create_tool_registry(),
                checkpointer=checkpointer,
                store=store,
            )

            result = graph.invoke(
                {
                    "messages": [
                        build_user_message(
                            request,
                            user_id,
                            request.conversation_id,
                        )
                    ]
                },
                {
                    "configurable": {
                        "thread_id": build_thread_id(
                            user_id,
                            request.conversation_id,
                        ),
                        "user_id": user_id,
                    }
                },
            )

    messages = result.get("messages", [])

    if not messages:
        raise RuntimeError("Agent returned no messages.")

    answer = messages[-1].content

    if not isinstance(answer, str):
        raise TypeError("Agent response must be a string.")

    return ChatResponse(
        conversation_id=request.conversation_id,
        answer=answer,
    )


def stream_chat(request: ChatRequest, user_id: str):
    ensure_data_dir()

    try:
        with SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
            checkpointer.setup()

            with open_memory_store(MEMORY_DB) as store:
                graph = create_tool_call_graph(
                    model=create_chat_model(),
                    registry=create_tool_registry(),
                    checkpointer=checkpointer,
                    store=store,
                )

                config = {
                    "configurable": {
                        "thread_id": build_thread_id(
                            user_id,
                            request.conversation_id,
                        ),
                        "user_id": user_id,
                    }
                }

                for chunk, metadata in graph.stream(
                    {
                        "messages": [
                            build_user_message(
                                request,
                                user_id,
                                request.conversation_id,
                            )
                        ]
                    },
                    config,
                    stream_mode="messages",
                ):
                    if not isinstance(chunk, AIMessageChunk):
                        continue

                    text = extract_chunk_text(chunk.content)

                    if text:
                        yield sse_event(
                            "token",
                            {"content": text},
                        )

        session_store.update_session_activity(
            user_id,
            request.conversation_id,
        )

        yield sse_event(
            "done",
            {
                "conversation_id": request.conversation_id,
            },
        )

    except Exception:
        logger.exception("Streaming chat failed")

        yield sse_event(
            "error",
            {"message": "Agent execution failed."},
        )


ChatHandler = Callable[
    [ChatRequest, str],
    ChatResponse,
]


# ==================================================
# FastAPI Application
# ==================================================


def create_app(
    chat_handler: ChatHandler = run_chat,
) -> FastAPI:

    app = FastAPI(
        title="Job AI Assistant",
        description="AI-powered job assistant API",
        version="0.1.0",
    )

    # ----------------------------------------------
    # Health Check
    # ----------------------------------------------

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # ----------------------------------------------
    # Chat
    # ----------------------------------------------

    @app.post(
        "/chat",
        response_model=ChatResponse,
    )
    def chat(
        request: ChatRequest,
        user_id: str = Depends(get_current_user_id),
    ):

        # Verify session ownership
        session = session_store.get_session(
            user_id,
            request.conversation_id,
        )

        if session is None:
            raise HTTPException(
                status_code=404,
                detail="Session not found.",
            )

        # Execute Agent
        response = chat_handler(
            request,
            user_id,
        )

        # Update session activity
        session_store.update_session_activity(
            user_id,
            request.conversation_id,
        )

        return response

    @app.post("/chat/stream")
    def chat_stream(
        request: ChatRequest,
        user_id: str = Depends(get_current_user_id),
    ):
        session = session_store.get_session(
            user_id,
            request.conversation_id,
        )

        if session is None:
            raise HTTPException(
                status_code=404,
                detail="Session not found.",
            )

        return StreamingResponse(
            stream_chat(request, user_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ----------------------------------------------
    # Create Session
    # ----------------------------------------------

    @app.post(
        "/sessions",
        response_model=SessionResponse,
        status_code=201,
    )
    def create_session(
        request: SessionCreateRequest,
        user_id: str = Depends(get_current_user_id),
    ):

        return session_store.create_session(
            user_id,
            request.title,
        )

    # ----------------------------------------------
    # List Sessions
    # ----------------------------------------------

    @app.get(
        "/sessions",
        response_model=list[SessionResponse],
    )
    def list_sessions(
        user_id: str = Depends(get_current_user_id),
    ):

        return session_store.list_sessions(user_id)

    # ----------------------------------------------
    # Get Conversation Messages
    # ----------------------------------------------

    @app.get(
        "/sessions/{conversation_id}/messages",
        response_model=list[MessageResponse],
    )
    def get_messages(
        conversation_id: str,
        user_id: str = Depends(get_current_user_id),
    ):

        # Verify session ownership
        session = session_store.get_session(
            user_id,
            conversation_id,
        )

        if session is None:
            raise HTTPException(
                status_code=404,
                detail="Session not found.",
            )

        ensure_data_dir()

        # 直接读取 LangGraph Checkpointer 线程:
        # 历史读取只依赖持久化数据,不创建聊天模型、工具注册表或记忆存储,
        # 模型服务不可用时历史仍可读。
        #
        # 读取通过与聊天图拓扑同构的只读图调用 get_state(LangGraph 公开
        # 状态读取),而不是手读最新检查点的 channel_values:待应用写入
        # (checkpoint_writes)只有公开状态读取才会按拓扑重算未处理任务并
        # 应用,直接读 channel_values 会漏掉中断运行留下的消息。
        with SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
            checkpointer.setup()

            snapshot = create_history_read_graph(checkpointer).get_state(
                {
                    "configurable": {
                        "thread_id": build_thread_id(
                            user_id,
                            conversation_id,
                        )
                    }
                }
            )

        if not snapshot.values:
            return []

        messages = snapshot.values.get(
            "messages",
            [],
        )

        result = []

        for message in messages:
            if message.type not in (
                "human",
                "ai",
            ):
                continue

            if not message.content:
                continue

            content = message.content

            attachments: list[AttachmentInfo] = []

            if message.type == "human":
                if isinstance(content, list):
                    # 块内容:文本块拼接为历史正文;image_url 块不回传
                    # base64,历史正文只留元数据。
                    content = "".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )

                for item in message.additional_kwargs.get("attachments", []):
                    if isinstance(item, dict):
                        attachments.append(AttachmentInfo(**item))

            if not isinstance(content, str):
                content = json.dumps(
                    content,
                    ensure_ascii=False,
                    default=str,
                )

            if not content and not attachments:
                continue

            result.append(
                MessageResponse(
                    role=("user" if message.type == "human" else "assistant"),
                    content=content,
                    attachments=attachments,
                )
            )

        return result

    # ----------------------------------------------
    # Delete Session
    # ----------------------------------------------

    @app.delete(
        "/sessions/{conversation_id}",
        status_code=204,
    )
    def delete_session(
        conversation_id: str,
        user_id: str = Depends(get_current_user_id),
    ):

        # Verify ownership
        session = session_store.get_session(
            user_id,
            conversation_id,
        )

        if session is None:
            raise HTTPException(
                status_code=404,
                detail="Session not found.",
            )

        thread_id = build_thread_id(
            user_id,
            conversation_id,
        )

        ensure_data_dir()

        # Delete LangGraph checkpoint
        with SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
            checkpointer.setup()

            checkpointer.delete_thread(thread_id)

        # M1-c:语音 blob 生命周期挂在会话上 —— 与线程/会话行同批清理。
        audio_store.delete_audio_for_session(
            user_id,
            conversation_id,
        )

        # Delete session metadata
        session_store.delete_session(
            user_id,
            conversation_id,
        )

        return Response(status_code=204)

    return app


app = create_app()
