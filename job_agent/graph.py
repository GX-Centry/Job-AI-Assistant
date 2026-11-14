from langgraph.graph import END, START, StateGraph

from job_agent.nodes.idle import idle_chain
from job_agent.state import AgentState


def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)

    builder.add_node("idle", idle_chain)
    builder.add_edge(START, "idle")
    builder.add_edge("idle", END)

    return builder.compile(checkpointer=checkpointer)


# Agent Server 使用，不注入自定义 Checkpointer
graph = build_graph()
