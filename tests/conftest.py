import pytest
from langgraph.checkpoint.memory import InMemorySaver

from job_agent.graph import build_graph


@pytest.fixture
def graph():
    return build_graph(checkpointer=InMemorySaver())


@pytest.fixture(autouse=True, scope="session")
def isolate_runtime_storage(tmp_path_factory):
    """从仓库根目录直接执行 pytest 也不触碰 data/ 运行目录。

    会话级自动夹具:把检查点、长期记忆、会话元数据的全部进程级默认路径
    先重定向到临时目录;单个测试仍可按需再次覆盖(如真实 SQLite 兼容性
    测试自行指定 tmp_path)。
    """

    from job_agent.api import app as api_app
    from job_agent.api import audio_store, session_store
    from job_agent.memory import store as memory_store

    run_dir = tmp_path_factory.mktemp("runtime-storage")

    monkeypatch = pytest.MonkeyPatch()

    monkeypatch.setattr(
        audio_store,
        "DB_PATH",
        run_dir / "agent_audio.sqlite",
    )

    monkeypatch.setattr(
        api_app,
        "CHECKPOINT_DB",
        str(run_dir / "agent_checkpoints.sqlite"),
    )

    monkeypatch.setattr(
        api_app,
        "MEMORY_DB",
        str(run_dir / "agent_memory.sqlite"),
    )

    monkeypatch.setattr(
        session_store,
        "DB_PATH",
        run_dir / "agent_sessions.sqlite",
    )

    monkeypatch.setattr(
        memory_store,
        "DEFAULT_DB_PATH",
        run_dir / "agent_memory.sqlite",
    )

    yield run_dir

    monkeypatch.undo()
