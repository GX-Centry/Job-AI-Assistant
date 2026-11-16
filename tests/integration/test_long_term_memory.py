import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableLambda
from langgraph.store.memory import InMemoryStore

from job_agent.graph import create_tool_call_graph
from job_agent.tools.factory import create_tool_registry


def build_test_graph(store, captured):
    def fake_model(messages):
        captured["messages"] = messages
        return AIMessage(content="你叫 Zoe。")

    class FakeModel:
        def bind_tools(self, tools):
            return RunnableLambda(fake_model)

    return create_tool_call_graph(
        model=FakeModel(),
        registry=create_tool_registry(),
        store=store,
    )


def test_agent_reads_long_term_memory():
    store = InMemoryStore()

    store.put(
        ("users", "user-001", "memories"),
        "profile",
        {"name": "Zoe"},
    )

    captured = {}
    graph = build_test_graph(store, captured)

    result = graph.invoke(
        {"messages": [HumanMessage(content="我叫什么名字？")]},
        {"configurable": {"user_id": "user-001"}},
    )

    system_messages = [m for m in captured["messages"] if isinstance(m, SystemMessage)]

    assert len(system_messages) == 1
    assert "Zoe" in system_messages[0].content
    assert result["messages"][-1].content == "你叫 Zoe。"


def test_memory_requires_user_id():
    store = InMemoryStore()
    captured = {}

    graph = build_test_graph(store, captured)

    with pytest.raises(ValueError, match="user_id"):
        graph.invoke({"messages": [HumanMessage(content="你好")]})
