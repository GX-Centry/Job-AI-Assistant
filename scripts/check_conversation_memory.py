import argparse
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from job_agent.graph import create_tool_call_graph
from job_agent.llm.factory import create_chat_model
from job_agent.tools.factory import create_tool_registry

DB_PATH = Path("data/agent_checkpoints.sqlite")
THREAD_PATH = Path("data/sqlite_test_thread_id.txt")
EXPECTED_MESSAGE = "请记住，我的名字叫 Zoe。"


def create_graph(checkpointer):
    return create_tool_call_graph(
        model=create_chat_model(),
        registry=create_tool_registry(),
        checkpointer=checkpointer,
    )


def write_checkpoint():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    thread_id = f"sqlite-test-{uuid4()}"
    THREAD_PATH.write_text(thread_id, encoding="utf-8")

    config = {"configurable": {"thread_id": thread_id}}

    with SqliteSaver.from_conn_string(str(DB_PATH)) as checkpointer:
        graph = create_graph(checkpointer)

        graph.invoke(
            {"messages": [HumanMessage(content=EXPECTED_MESSAGE)]},
            config,
        )

    print("SQLite checkpoint WRITE: SUCCESS")
    print("Thread ID:", thread_id)


def read_checkpoint():
    if not THREAD_PATH.exists():
        raise RuntimeError("Thread ID file does not exist.")

    thread_id = THREAD_PATH.read_text(encoding="utf-8")

    config = {"configurable": {"thread_id": thread_id}}

    with SqliteSaver.from_conn_string(str(DB_PATH)) as checkpointer:
        graph = create_graph(checkpointer)
        snapshot = graph.get_state(config)

        messages = (snapshot.values or {}).get("messages", [])

    found = any(
        getattr(message, "content", None) == EXPECTED_MESSAGE for message in messages
    )

    if not found:
        raise RuntimeError("SQLite checkpoint recovery failed.")

    print("SQLite checkpoint READ: SUCCESS")
    print("Recovered message:", EXPECTED_MESSAGE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["write", "read"])
    args = parser.parse_args()

    if args.action == "write":
        write_checkpoint()
    else:
        read_checkpoint()


if __name__ == "__main__":
    main()
