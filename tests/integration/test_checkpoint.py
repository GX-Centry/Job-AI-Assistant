from langchain_core.messages import HumanMessage


def test_checkpoint_persistence(graph):
    config = {"configurable": {"thread_id": "test-persistence"}}

    graph.invoke({"messages": [HumanMessage(content="第一轮")]}, config)

    result = graph.invoke({"messages": [HumanMessage(content="第二轮")]}, config)

    assert [m.content for m in result["messages"]] == ["第一轮", "第二轮"]


def test_thread_isolation(graph):
    config_a = {"configurable": {"thread_id": "thread-A"}}
    config_b = {"configurable": {"thread_id": "thread-B"}}

    graph.invoke({"messages": [HumanMessage(content="用户A")]}, config_a)
    graph.invoke({"messages": [HumanMessage(content="用户B")]}, config_b)

    state_a = graph.get_state(config_a)
    state_b = graph.get_state(config_b)

    assert state_a.values["messages"][0].content == "用户A"
    assert state_b.values["messages"][0].content == "用户B"
