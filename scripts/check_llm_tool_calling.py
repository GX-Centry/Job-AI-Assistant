from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)

from job_agent.graph import create_tool_call_graph
from job_agent.llm.factory import create_chat_model
from job_agent.tools.factory import create_tool_registry


def main():
    model = create_chat_model()
    registry = create_tool_registry()

    graph = create_tool_call_graph(
        model=model,
        registry=registry,
    )

    result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "你必须调用 text_length 工具，"
                        "参数 text 必须为 hello。"
                        "工具执行后，告诉我返回结果。"
                    )
                )
            ]
        },
        config={
            "run_name": "real_tool_calling_test",
            "tags": [
                "job-agent",
                "real-llm",
                "tool-calling",
            ],
            "metadata": {
                "test_case": "text_length",
                "stage": "integration",
            },
        },
    )

    messages = result["messages"]

    tool_calls = [
        call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]

    tool_results = [message for message in messages if isinstance(message, ToolMessage)]

    print("Tool calls:", tool_calls)
    print("Tool results:", [m.content for m in tool_results])

    if not tool_calls:
        raise RuntimeError("Model did not call any tool.")

    if not tool_results:
        raise RuntimeError("Tool execution failed.")

    if not any(
        message.name == "text_length" and message.content == "5"
        for message in tool_results
    ):
        raise RuntimeError("Unexpected tool execution result.")

    print("Tool Calling: SUCCESS")
    print("Final response:", messages[-1].content)


if __name__ == "__main__":
    main()
