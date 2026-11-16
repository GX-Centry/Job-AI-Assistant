from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from job_agent.state import AgentState
from job_agent.tools.executor import create_tool_node
from job_agent.tools.factory import create_tool_registry


def test_tool_node_execution():
    registry = create_tool_registry()
    node = create_tool_node(registry)

    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "text_length",
                "args": {"text": "hello"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )

    # 将 ToolNode 接入临时 Graph
    builder = StateGraph(AgentState)
    builder.add_node("tools", node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)

    graph = builder.compile()

    # 通过 Graph 执行
    result = graph.invoke({"messages": [message]})

    tool_message = result["messages"][-1]

    assert isinstance(tool_message, ToolMessage)
    assert tool_message.content == "5"
    assert tool_message.name == "text_length"
    assert tool_message.tool_call_id == "call_1"
