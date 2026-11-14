from job_agent.nodes.idle import idle_logic


def test_idle_logic():
    assert idle_logic({"messages": []}) == {}
