from job_agent.memory.store import open_memory_store


def test_memory_persistence(tmp_path):
    db_path = tmp_path / "memory.sqlite"
    namespace = ("users", "user-001", "memories")

    with open_memory_store(db_path) as store:
        store.put(namespace, "profile", {"name": "Zoe"})

    with open_memory_store(db_path) as store:
        item = store.get(namespace, "profile")

    assert item is not None
    assert item.value["name"] == "Zoe"
