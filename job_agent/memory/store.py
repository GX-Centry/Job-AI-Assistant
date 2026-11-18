from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.store.sqlite import SqliteStore

DEFAULT_DB_PATH = Path("data/agent_memory.sqlite")


@contextmanager
def open_memory_store(
    db_path: str | Path | None = None,
) -> Iterator[SqliteStore]:
    # 默认路径在调用时解析:默认参数若在 def 时固化,
    # 测试隔离(打桩 DEFAULT_DB_PATH)对无参数调用将失效。
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with SqliteStore.from_conn_string(str(path)) as store:
        store.setup()
        yield store
