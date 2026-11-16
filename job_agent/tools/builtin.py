from langchain_core.tools import tool

from job_agent.tools.schemas import TextLengthInput


@tool(args_schema=TextLengthInput)
def text_length(text: str) -> int:
    """Calculate the number of characters in text."""
    return len(text)
