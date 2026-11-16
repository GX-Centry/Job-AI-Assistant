from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

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


def test_get_messages(
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

    class FakeGraph:
        def get_state(self, config):
            assert config["configurable"]["thread_id"] == api_module.build_thread_id(
                "user-001",
                conversation_id,
            )

            return SimpleNamespace(
                values={
                    "messages": [
                        SimpleNamespace(
                            type="human",
                            content="hello",
                        ),
                        SimpleNamespace(
                            type="tool",
                            content="5",
                        ),
                        SimpleNamespace(
                            type="ai",
                            content="Hello from Agent",
                        ),
                    ]
                }
            )

    patch_graph_dependencies(
        monkeypatch,
        FakeGraph(),
    )

    response = test_client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200

    messages = response.json()

    assert len(messages) == 2

    assert messages[0] == {
        "role": "user",
        "content": "hello",
    }

    assert messages[1] == {
        "role": "assistant",
        "content": "Hello from Agent",
    }


def test_get_messages_without_checkpoint(
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

    class FakeGraph:
        def get_state(self, config):
            return None

    patch_graph_dependencies(
        monkeypatch,
        FakeGraph(),
    )

    response = test_client.get(
        f"/sessions/{conversation_id}/messages",
        headers={"X-API-Key": "test-token"},
    )

    assert response.status_code == 200
    assert response.json() == []


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
