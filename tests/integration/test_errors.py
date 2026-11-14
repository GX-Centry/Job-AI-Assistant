import pytest
from langgraph.graph import END, START, StateGraph

from job_agent.state import AgentState


def test_node_exception():
    def broken_node(state):
        raise RuntimeError("intentional test failure")

    builder = StateGraph(AgentState)
    builder.add_node("broken", broken_node)
    builder.add_edge(START, "broken")
    builder.add_edge("broken", END)

    graph = builder.compile()

    with pytest.raises(RuntimeError, match="intentional test failure"):
        graph.invoke({"messages": []})
