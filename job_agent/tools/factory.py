from job_agent.tools.builtin import text_length, transcribe_audio
from job_agent.tools.registry import ToolRegistry


def create_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(text_length)
    registry.register(transcribe_audio)
    return registry
