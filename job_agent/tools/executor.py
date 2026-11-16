from langgraph.prebuilt import ToolNode

from job_agent.tools.registry import ToolRegistry


def create_tool_node(registry: ToolRegistry) -> ToolNode:
    return ToolNode(registry.list_tools())
