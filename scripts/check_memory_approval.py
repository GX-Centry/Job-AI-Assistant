from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from job_agent.llm.factory import create_chat_model
from job_agent.memory.approval_graph import (
    create_memory_approval_graph,
)
from job_agent.memory.store import open_memory_store


def main():
    Path("data").mkdir(exist_ok=True)

    with (
        open_memory_store("data/agent_memory.sqlite") as store,
        SqliteSaver.from_conn_string("data/agent_checkpoints.sqlite") as checkpointer,
    ):
        graph = create_memory_approval_graph(
            create_chat_model(),
            checkpointer,
            store,
        )

        config = {"configurable": {"thread_id": f"approval-{uuid4()}"}}

        user_id = "demo-user-001"

        user_text = input("User: ").strip()

        result = graph.invoke(
            {
                "user_id": user_id,
                "user_messages": [user_text],
            },
            config,
        )

        interrupts = result.get("__interrupt__", [])

        if not interrupts:
            print("Status:", result["status"])
            return

        print("\n待审批记忆：")
        print(interrupts[0].value["candidate"])

        decision = input("\n输入 SAVE 确认保存，其他输入拒绝：").strip() == "SAVE"

        result = graph.invoke(
            Command(resume=decision),
            config,
        )

        print("Final status:", result["status"])


if __name__ == "__main__":
    main()
