from typing import TypedDict, Annotated
from langchain_core.messages import AnyMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

# 定义状态
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

# 空节点
def idle(state: AgentState):
    return {}

# 构建流程
builder = StateGraph(AgentState)
builder.add_node("idle", idle)
builder.add_edge(START, "idle")
builder.add_edge("idle", END)

graph = builder.compile()

if __name__ == "__main__":
    print(graph.invoke({"messages": []}))