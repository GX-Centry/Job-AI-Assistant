from langgraph.checkpoint.memory import InMemorySaver

from job_agent.graph import build_graph

graph = build_graph(checkpointer=InMemorySaver())

if __name__ == "__main__":
    print(graph.invoke({"messages": []}))
