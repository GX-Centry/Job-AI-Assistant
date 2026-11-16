from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from job_agent.graph import create_tool_call_graph
from job_agent.llm.factory import create_chat_model
from job_agent.memory.approval_graph import (
    create_memory_approval_graph,
)
from job_agent.memory.store import open_memory_store
from job_agent.tools.factory import create_tool_registry

USER_ID = "demo-user-001"
CHECKPOINT_DB = "data/agent_checkpoints.sqlite"
MEMORY_DB = "data/agent_memory.sqlite"


def main():
    model = create_chat_model()
    registry = create_tool_registry()

    chat_config = {
        "configurable": {
            "thread_id": f"chat-{USER_ID}",
            "user_id": USER_ID,
        }
    }

    with (
        open_memory_store(MEMORY_DB) as store,
        SqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer,
    ):
        chat_graph = create_tool_call_graph(
            model=model,
            registry=registry,
            checkpointer=checkpointer,
            store=store,
        )

        approval_graph = create_memory_approval_graph(
            model=model,
            checkpointer=checkpointer,
            store=store,
        )

        print("JOB AI ASSISTANT")
        print("输入 /remember 内容 保存长期记忆")
        print("输入 /exit 退出")

        while True:
            user_text = input("\nYou: ").strip()

            if user_text == "/exit":
                break

            if not user_text:
                continue

            # 长期记忆审批流程
            if user_text.startswith("/remember "):
                memory_text = user_text[len("/remember ") :].strip()

                if not memory_text:
                    print("Memory content cannot be empty.")
                    continue

                approval_config = {"configurable": {"thread_id": f"memory-{uuid4()}"}}

                result = approval_graph.invoke(
                    {
                        "user_id": USER_ID,
                        "user_messages": [memory_text],
                    },
                    approval_config,
                )

                interrupts = result.get("__interrupt__", [])

                if not interrupts:
                    print("Memory status:", result["status"])
                    continue

                print("\nMemory candidate:")
                print(interrupts[0].value["candidate"])

                decision = (
                    input("\n输入 SAVE 确认保存，其他输入拒绝：").strip() == "SAVE"
                )

                result = approval_graph.invoke(
                    Command(resume=decision),
                    approval_config,
                )

                print("Memory status:", result["status"])
                continue

            # 普通 Agent 对话
            result = chat_graph.invoke(
                {"messages": [HumanMessage(content=user_text)]},
                chat_config,
            )

            print("\nAgent:", result["messages"][-1].content)


if __name__ == "__main__":
    main()
