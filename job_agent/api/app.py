import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Response,
)
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field, field_validator

from job_agent.api import session_store
from job_agent.api.security import get_current_user_id
from job_agent.graph import create_tool_call_graph
from job_agent.llm.factory import create_chat_model
from job_agent.memory.store import open_memory_store
from job_agent.tools.factory import create_tool_registry

CHECKPOINT_DB = "data/agent_checkpoints.sqlite"
MEMORY_DB = "data/agent_memory.sqlite"


# ==================================================
# Data Models
# ==================================================


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=10000,
    )

    conversation_id: str = Field(
        min_length=1,
        max_length=128,
    )

    @field_validator("message", "conversation_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field cannot be empty.")

        return value.strip()


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str


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


# ==================================================
# Utilities
# ==================================================


def ensure_data_dir() -> None:
    Path("data").mkdir(
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
                {"messages": [HumanMessage(content=request.message)]},
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

        # Retrieve LangGraph checkpoint
        with SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
            checkpointer.setup()

            with open_memory_store(MEMORY_DB) as store:
                graph = create_tool_call_graph(
                    model=create_chat_model(),
                    registry=create_tool_registry(),
                    checkpointer=checkpointer,
                    store=store,
                )

                snapshot = graph.get_state(
                    {
                        "configurable": {
                            "thread_id": build_thread_id(
                                user_id,
                                conversation_id,
                            )
                        }
                    }
                )

        if not snapshot or not snapshot.values:
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

            if not isinstance(content, str):
                content = json.dumps(
                    content,
                    ensure_ascii=False,
                    default=str,
                )

            result.append(
                MessageResponse(
                    role=("user" if message.type == "human" else "assistant"),
                    content=content,
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

        # Delete session metadata
        session_store.delete_session(
            user_id,
            conversation_id,
        )

        return Response(status_code=204)

    return app


app = create_app()
