import pytest

from job_agent.api import session_store


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        session_store,
        "DB_PATH",
        tmp_path / "sessions.sqlite",
    )


def test_create_session(isolated_db):
    session = session_store.create_session("user-001")

    assert session["conversation_id"]
    assert session["title"] == "New conversation"


def test_list_sessions(isolated_db):
    session_store.create_session("user-001")
    session_store.create_session("user-001")

    sessions = session_store.list_sessions("user-001")

    assert len(sessions) == 2


def test_user_session_isolation(isolated_db):
    session = session_store.create_session("user-001")

    assert (
        session_store.get_session(
            "user-002",
            session["conversation_id"],
        )
        is None
    )


def test_get_session(isolated_db):
    session = session_store.create_session("user-001")

    result = session_store.get_session(
        "user-001",
        session["conversation_id"],
    )

    assert result is not None


def test_update_session_activity(isolated_db):
    session = session_store.create_session("user-001")

    assert session_store.update_session_activity(
        "user-001",
        session["conversation_id"],
    )


def test_empty_user_id_rejected(isolated_db):
    with pytest.raises(ValueError):
        session_store.create_session("")
