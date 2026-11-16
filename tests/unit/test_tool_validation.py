import pytest
from pydantic import ValidationError

from job_agent.tools.builtin import text_length


def test_valid_input():
    assert text_length.invoke({"text": "hello"}) == 5


def test_empty_input():
    with pytest.raises(ValidationError):
        text_length.invoke({"text": ""})


def test_missing_parameter():
    with pytest.raises(ValidationError):
        text_length.invoke({})


def test_excessive_length():
    with pytest.raises(ValidationError):
        text_length.invoke({"text": "a" * 1001})


def test_wrong_type():
    with pytest.raises(ValidationError):
        text_length.invoke({"text": 123})
