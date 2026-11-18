from job_agent.memory import store
from job_agent.memory.store import open_memory_store


def test_open_memory_store_without_args_follows_module_default(tmp_path, monkeypatch):
    """无参数 open_memory_store() 必须在**调用时**解析 DEFAULT_DB_PATH。

    def-time 默认参数会固化旧路径,使 tests/conftest.py 的默认路径重定向
    保证失效。用例把 cwd 切到临时目录:修复前,旧绑定把库写到
    <tmp>/data/(相对路径在 def 时固化),被打桩的目标路径不存在 → 失败;
    修复后,库精确落在被打桩的 DEFAULT_DB_PATH。任何情况下都不触碰真实
    data/ 业务库。
    """

    target = tmp_path / "isolated-runtime" / "agent_memory.sqlite"

    monkeypatch.setattr(store, "DEFAULT_DB_PATH", target)
    monkeypatch.chdir(tmp_path)

    namespace = ("users", "user-001", "memories")

    with open_memory_store() as opened:
        opened.put(namespace, "profile", {"city": "杭州"})

    assert target.exists()

    with open_memory_store() as opened:
        item = opened.get(namespace, "profile")

    assert item is not None
    assert item.value == {"city": "杭州"}

    # 旧绑定特征不得出现:固化的相对路径不应生成 <cwd>/data 库。
    assert not (tmp_path / "data" / "agent_memory.sqlite").exists()


def test_memory_persistence(tmp_path):
    db_path = tmp_path / "memory.sqlite"
    namespace = ("users", "user-001", "memories")

    with open_memory_store(db_path) as store:
        store.put(namespace, "profile", {"name": "Zoe"})

    with open_memory_store(db_path) as store:
        item = store.get(namespace, "profile")

    assert item is not None
    assert item.value["name"] == "Zoe"
