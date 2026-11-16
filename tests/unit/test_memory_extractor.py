import pytest

from job_agent.memory.extractor import (
    MemoryCandidate,
    extract_memory_candidate,
    validate_memory_candidate,
)


def test_valid_memory_candidate():
    candidate = MemoryCandidate(
        should_save=True,
        key="preferred_language",
        value="Chinese",
        source_quote="我希望使用中文回答。",
        reason="Stable language preference.",
    )

    validate_memory_candidate(
        candidate,
        ["我希望使用中文回答。"],
    )


def test_no_memory_candidate():
    candidate = MemoryCandidate(
        should_save=False,
        key="",
        value="",
        source_quote="",
        reason="No durable information.",
    )

    validate_memory_candidate(candidate, ["你好"])


def test_invalid_memory_key():
    candidate = MemoryCandidate(
        should_save=True,
        key="Invalid Key",
        value="Chinese",
        source_quote="我希望使用中文回答。",
        reason="Preference.",
    )

    with pytest.raises(ValueError, match="key"):
        validate_memory_candidate(
            candidate,
            ["我希望使用中文回答。"],
        )


def test_invalid_source_quote():
    candidate = MemoryCandidate(
        should_save=True,
        key="preferred_language",
        value="Chinese",
        source_quote="我喜欢英文。",
        reason="Preference.",
    )

    with pytest.raises(ValueError, match="Source quote"):
        validate_memory_candidate(
            candidate,
            ["我希望使用中文回答。"],
        )


def test_extract_with_fake_model():
    candidate = MemoryCandidate(
        should_save=True,
        key="preferred_language",
        value="Chinese",
        source_quote="我希望使用中文回答。",
        reason="Stable preference.",
    )

    class FakeStructuredModel:
        def invoke(self, messages):
            return candidate

    class FakeModel:
        def with_structured_output(self, schema, method):
            assert schema is MemoryCandidate
            assert method == "function_calling"
            return FakeStructuredModel()

    result = extract_memory_candidate(
        FakeModel(),
        ["我希望使用中文回答。"],
    )

    assert result.value == "Chinese"


def test_empty_messages_rejected():
    with pytest.raises(ValueError, match="empty"):
        extract_memory_candidate(object(), [])
