import argparse

from job_agent.memory.store import open_memory_store

DB_PATH = "data/agent_memory.sqlite"
USER_ID = "demo-user-001"
NAMESPACE = ("users", USER_ID, "memories")


def write_memory():
    with open_memory_store(DB_PATH) as store:
        store.put(
            NAMESPACE,
            "profile",
            {
                "name": "Zoe",
                "preferred_language": "zh-CN",
            },
        )

    print("Long-term memory WRITE: SUCCESS")


def read_memory():
    with open_memory_store(DB_PATH) as store:
        item = store.get(NAMESPACE, "profile")

    if item is None:
        raise RuntimeError("Memory not found.")

    print("Recovered memory:", item.value)

    if item.value.get("name") != "Zoe":
        raise RuntimeError("Memory validation failed.")

    print("Long-term memory READ: SUCCESS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["write", "read"])
    args = parser.parse_args()

    if args.action == "write":
        write_memory()
    else:
        read_memory()


if __name__ == "__main__":
    main()
