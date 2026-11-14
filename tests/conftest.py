import pytest
from langgraph.checkpoint.memory import InMemorySaver

from job_agent.graph import build_graph


@pytest.fixture
def graph():
    return build_graph(checkpointer=InMemorySaver())
