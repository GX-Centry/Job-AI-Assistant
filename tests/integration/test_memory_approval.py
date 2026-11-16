from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from job_agent.memory.approval_graph import (
    create_memory_approval_graph,
)
from job_agent.memory.extractor import MemoryCandidate


class FakeStructuredModel:
    def __init__(self, candidate):
        self.candidate = candidate

    def invoke(self, messages):
        return self.candidate


class FakeModel:
    def __init__(self, candidate):
        self.candidate = candidate

    def with_structured_output(self, schema, method):
        return FakeStructuredModel(self.candidate)


def build_graph(candidate):
    store = InMemoryStore()
    checkpointer = InMemorySaver()

    graph = create_memory_approval_graph(
        FakeModel(candidate),
        checkpointer,
        store,
    )

    return graph, store


def make_candidate(should_save=True):
    return MemoryCandidate(
        should_save=should_save,
        key="language_preference" if should_save else "",
        value="中文" if should_save else "",
        source_quote="我希望使用中文回答。" if should_save else "",
        reason="User preference." if should_save else "",
    )


def test_approved_memory_is_saved():
    graph, store = build_graph(make_candidate())

    config = {"configurable": {"thread_id": "approval-001"}}

    result = graph.invoke(
        {
            "user_id": "user-001",
            "user_messages": ["我希望使用中文回答。"],
        },
        config,
    )

    assert "__interrupt__" in result

    result = graph.invoke(
        Command(resume=True),
        config,
    )

    assert result["status"] == "saved"

    item = store.get(
        ("users", "user-001", "memories"),
        "profile",
    )

    assert item.value["language_preference"] == "中文"


def test_rejected_memory_is_not_saved():
    graph, store = build_graph(make_candidate())

    config = {"configurable": {"thread_id": "approval-002"}}

    graph.invoke(
        {
            "user_id": "user-001",
            "user_messages": ["我希望使用中文回答。"],
        },
        config,
    )

    result = graph.invoke(
        Command(resume=False),
        config,
    )

    assert result["status"] == "rejected"

    assert (
        store.get(
            ("users", "user-001", "memories"),
            "profile",
        )
        is None
    )


def test_no_candidate_skips_approval():
    graph, _store = build_graph(make_candidate(should_save=False))

    result = graph.invoke(
        {
            "user_id": "user-001",
            "user_messages": ["你好"],
        },
        {"configurable": {"thread_id": "approval-003"}},
    )

    assert result["status"] == "no_candidate"
    assert "__interrupt__" not in result
