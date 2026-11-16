import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field


class MemoryCandidate(BaseModel):
    should_save: bool = Field(
        description="Whether a durable user memory should be saved."
    )
    key: str = Field(
        description="A lowercase snake_case memory key. Empty if no candidate."
    )
    value: str = Field(description="The concise memory value. Empty if no candidate.")
    source_quote: str = Field(description="An exact quote from the user's message.")
    reason: str = Field(description="Why this information is worth remembering.")


def validate_memory_candidate(
    candidate: MemoryCandidate,
    user_messages: list[str],
) -> None:
    if not candidate.should_save:
        return

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", candidate.key):
        raise ValueError("Invalid memory key.")

    if not candidate.value.strip() or len(candidate.value) > 300:
        raise ValueError("Invalid memory value.")

    transcript = "\n".join(user_messages)

    if not candidate.source_quote or candidate.source_quote not in transcript:
        raise ValueError("Source quote does not match user messages.")


def extract_memory_candidate(
    model,
    user_messages: list[str],
) -> MemoryCandidate:
    if not user_messages or not any(message.strip() for message in user_messages):
        raise ValueError("User messages cannot be empty.")

    structured_model = model.with_structured_output(
        MemoryCandidate,
        method="function_calling",
    )

    candidate = structured_model.invoke(
        [
            SystemMessage(
                content=(
                    "Extract at most one durable user memory. "
                    "Only extract facts explicitly stated by the user. "
                    "Do not infer or invent information. "
                    "Ignore temporary situations and hypothetical statements. "
                    "Never extract passwords, API keys, authentication tokens, "
                    "financial credentials, government identifiers, or "
                    "sensitive health information. "
                    "source_quote must exactly match the user's original text. "
                    "If nothing is worth remembering, set should_save=false "
                    "and leave key, value and source_quote empty."
                )
            ),
            HumanMessage(content="\n".join(user_messages)),
        ]
    )

    validate_memory_candidate(candidate, user_messages)

    return candidate
