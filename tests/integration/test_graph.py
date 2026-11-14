from langchain_core.messages import HumanMessage


def test_empty_input(graph):
    config = {"configurable": {"thread_id": "test-empty"}}
    result = graph.invoke({"messages": []}, config)
    assert result["messages"] == []


def test_message_preservation(graph):
    config = {"configurable": {"thread_id": "test-message"}}
    msg = HumanMessage(content="hello")

    result = graph.invoke({"messages": [msg]}, config)

    assert result["messages"][0].content == "hello"
