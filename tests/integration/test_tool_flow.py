from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableLambda

from job_agent.graph import create_tool_call_graph
from job_agent.tools.factory import create_tool_registry


def test_agent_tool_call_roundtrip():

    def fake_model(messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="The text has 5 characters.")

        return AIMessage(
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

    class FakeToolCallingModel:
        def bind_tools(self, tools):
            assert [tool.name for tool in tools] == ["text_length"]
            return RunnableLambda(fake_model)

    model = FakeToolCallingModel()

    graph = create_tool_call_graph(
        model=model,
        registry=create_tool_registry(),
    )

    result = graph.invoke({"messages": [HumanMessage(content="Count hello")]})

    messages = result["messages"]

    assert any(isinstance(m, ToolMessage) and m.content == "5" for m in messages)

    assert messages[-1].content == ("The text has 5 characters.")
