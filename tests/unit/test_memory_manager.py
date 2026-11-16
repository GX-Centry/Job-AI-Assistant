import pytest
from langgraph.store.memory import InMemoryStore

from job_agent.memory.manager import (
    delete_memory,
    get_memory,
    save_memory,
)


def test_memory_crud():
    store = InMemoryStore()

    save_memory(
        store,
        "user-001",
        "profile",
        {"name": "Zoe"},
    )

    assert get_memory(store, "user-001", "profile") == {"name": "Zoe"}

    delete_memory(store, "user-001", "profile")

    assert get_memory(store, "user-001", "profile") is None


def test_memory_user_isolation():
    store = InMemoryStore()

    save_memory(
        store,
        "user-001",
        "profile",
        {"name": "Zoe"},
    )

    assert get_memory(store, "user-002", "profile") is None


def test_empty_user_id_rejected():
    store = InMemoryStore()

    with pytest.raises(ValueError):
        save_memory(
            store,
            "",
            "profile",
            {"name": "Zoe"},
        )


def test_invalid_memory_value_rejected():
    store = InMemoryStore()

    with pytest.raises(TypeError):
        save_memory(
            store,
            "user-001",
            "profile",
            "Zoe",
        )
