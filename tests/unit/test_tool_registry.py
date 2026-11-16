import pytest

from job_agent.tools.builtin import text_length
from job_agent.tools.factory import create_tool_registry


def test_register_and_get():
    registry = create_tool_registry()
    assert registry.get("text_length") is text_length


def test_duplicate_registration():
    registry = create_tool_registry()

    with pytest.raises(ValueError):
        registry.register(text_length)


def test_unknown_tool():
    registry = create_tool_registry()

    with pytest.raises(KeyError):
        registry.get("unknown")


def test_list_tools():
    registry = create_tool_registry()

    assert len(registry.list_tools()) == 1


def test_tool_execution():
    assert text_length.invoke({"text": "LangGraph"}) == 9
