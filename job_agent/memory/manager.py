from typing import Any

from langgraph.store.base import BaseStore


def _get_namespace(user_id: str) -> tuple[str, ...]:
    if not user_id or not user_id.strip():
        raise ValueError("user_id cannot be empty.")

    return ("users", user_id, "memories")


def save_memory(
    store: BaseStore,
    user_id: str,
    key: str,
    value: dict[str, Any],
) -> None:
    if not key or not key.strip():
        raise ValueError("Memory key cannot be empty.")

    if not isinstance(value, dict):
        raise TypeError("Memory value must be a dictionary.")

    store.put(
        _get_namespace(user_id),
        key,
        value,
    )


def get_memory(
    store: BaseStore,
    user_id: str,
    key: str,
) -> dict[str, Any] | None:
    item = store.get(
        _get_namespace(user_id),
        key,
    )

    return None if item is None else item.value


def delete_memory(
    store: BaseStore,
    user_id: str,
    key: str,
) -> None:
    store.delete(
        _get_namespace(user_id),
        key,
    )
