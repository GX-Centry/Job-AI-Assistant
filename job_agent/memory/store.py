from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.store.sqlite import SqliteStore

DEFAULT_DB_PATH = Path("data/agent_memory.sqlite")


@contextmanager
def open_memory_store(
    db_path: str | Path = DEFAULT_DB_PATH,
) -> Iterator[SqliteStore]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with SqliteStore.from_conn_string(str(path)) as store:
        store.setup()
        yield store
