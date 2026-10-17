"""Agent tool implementations, adapters, integrations, and catalog."""

from app.agent.tools import builtin
from app.agent.tools.base import Tool
from app.agent.tools.catalog import ToolCatalog
from app.agent.tools.mcp import MCPTool
from app.agent.tools.static import StaticTool

TOOL_CATALOG = ToolCatalog()
for _tool_name, _tool_function in builtin.AGENT_TOOL_FUNCTIONS.items():
    TOOL_CATALOG.register(StaticTool(_tool_function))

# Keep the function-oriented names available while callers migrate to the catalog.
AGENT_TOOL_NAMES = builtin.AGENT_TOOL_NAMES
AGENT_TOOL_FUNCTIONS = builtin.AGENT_TOOL_FUNCTIONS
RETRIEVAL_TOOL_NAMES = builtin.RETRIEVAL_TOOL_NAMES

for _name in AGENT_TOOL_FUNCTIONS:
    globals()[_name] = getattr(builtin, _name)

for _name in (
    "DEFAULT_DOCUMENT_LIMIT",
    "DEFAULT_CHUNK_LIMIT",
    "DEFAULT_DOCUMENT_CHARS",
    "DEFAULT_DOCUMENT_LINES",
    "DEFAULT_GREP_LIMIT",
    "DEFAULT_GREP_CONTEXT_CHARS",
    "_bounded_limit",
    "_bounded_max_chars",
    "_bounded_positive_int",
    "_bounded_context_chars",
    "_normalized_documents",
    "_markdown_path_for_document",
):
    globals()[_name] = getattr(builtin, _name)


def render_tool_descriptions() -> str:
    """Render planner instructions from the registered tool interfaces."""
    return TOOL_CATALOG.render_descriptions()

from app.agent.tools.integrations.mcp_registry import call_mcp_tool, get_mcp_registry

__all__ = [
    "Tool", "StaticTool", "MCPTool", "ToolCatalog", "TOOL_CATALOG",
    "AGENT_TOOL_NAMES", "AGENT_TOOL_FUNCTIONS", "RETRIEVAL_TOOL_NAMES",
    "call_mcp_tool", "get_mcp_registry", "render_tool_descriptions",
    *AGENT_TOOL_NAMES,
]
