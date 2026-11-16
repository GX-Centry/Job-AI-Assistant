from langchain_core.messages import HumanMessage

from job_agent.graph import create_tool_call_graph
from job_agent.llm.factory import create_chat_model
from job_agent.memory.store import open_memory_store
from job_agent.tools.factory import create_tool_registry


def main():
    with open_memory_store("data/agent_memory.sqlite") as store:
        graph = create_tool_call_graph(
            model=create_chat_model(),
            registry=create_tool_registry(),
            store=store,
        )

        result = graph.invoke(
            {"messages": [HumanMessage(content="根据你保存的记忆，我叫什么名字？")]},
            {"configurable": {"user_id": "demo-user-001"}},
        )

    response = result["messages"][-1].content

    print("Agent response:", response)

    if "Zoe" not in response:
        raise RuntimeError("Long-term memory retrieval failed.")

    print("Agent Long-term Memory: SUCCESS")


if __name__ == "__main__":
    main()
