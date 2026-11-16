from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import interrupt

from job_agent.memory.extractor import (
    MemoryCandidate,
    extract_memory_candidate,
)
from job_agent.memory.manager import get_memory, save_memory


class MemoryApprovalState(TypedDict, total=False):
    user_id: str
    user_messages: list[str]
    candidate: dict[str, Any]
    approved: bool
    status: str


def create_memory_approval_graph(model, checkpointer, store: BaseStore):

    def extract_node(state: MemoryApprovalState):
        user_id = state.get("user_id", "")

        if not user_id.strip():
            raise ValueError("user_id is required.")

        candidate = extract_memory_candidate(
            model,
            state.get("user_messages", []),
        )

        return {
            "candidate": candidate.model_dump(),
            "status": ("pending" if candidate.should_save else "no_candidate"),
        }

    def route_after_extract(state):
        candidate = state.get("candidate", {})
        return "approval" if candidate.get("should_save") else "end"

    def approval_node(state: MemoryApprovalState):
        decision = interrupt(
            {
                "message": "请审核是否保存这条长期记忆。",
                "candidate": state["candidate"],
            }
        )

        if not isinstance(decision, bool):
            raise TypeError("Approval decision must be boolean.")

        return {
            "approved": decision,
            "status": "approved" if decision else "rejected",
        }

    def route_after_approval(state):
        return "save" if state.get("approved") is True else "end"

    def save_node(state: MemoryApprovalState, runtime: Runtime):
        if runtime.store is None:
            raise RuntimeError("Memory store is unavailable.")

        candidate = MemoryCandidate.model_validate(state["candidate"])

        user_id = state["user_id"]

        profile = (
            get_memory(
                runtime.store,
                user_id,
                "profile",
            )
            or {}
        )

        profile[candidate.key] = candidate.value

        save_memory(
            runtime.store,
            user_id,
            "profile",
            profile,
        )

        return {"status": "saved"}

    builder = StateGraph(MemoryApprovalState)

    builder.add_node("extract", extract_node)
    builder.add_node("approval", approval_node)
    builder.add_node("save", save_node)

    builder.add_edge(START, "extract")

    builder.add_conditional_edges(
        "extract",
        route_after_extract,
        {
            "approval": "approval",
            "end": END,
        },
    )

    builder.add_conditional_edges(
        "approval",
        route_after_approval,
        {
            "save": "save",
            "end": END,
        },
    )

    builder.add_edge("save", END)

    return builder.compile(
        checkpointer=checkpointer,
        store=store,
    )
