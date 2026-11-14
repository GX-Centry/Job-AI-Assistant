from agent import graph
from langchain_core.messages import HumanMessage


def test_empty_input():
    result = graph.invoke({"messages": []})
    assert result["messages"] == []


def test_message_preservation():
    msg = HumanMessage(content="hello")
    result = graph.invoke({"messages": [msg]})
    assert result["messages"][0].content == "hello"

import pytest
from langgraph.graph import StateGraph, START, END
from agent import AgentState

def test_node_exception():
    def broken_node(state):
        raise RuntimeError("intentional test failure")

    builder = StateGraph(AgentState)
    builder.add_node("broken", broken_node)
    builder.add_edge(START, "broken")
    builder.add_edge("broken", END)

    broken_graph = builder.compile()

    with pytest.raises(RuntimeError, match="intentional test failure"):
        broken_graph.invoke({"messages": []})