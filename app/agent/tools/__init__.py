"""Agent tool implementations, adapters, integrations, and catalog."""

from app.agent.tools import builtin
from app.agent.tools.base import Tool
from app.agent.tools.catalog import ToolCatalog, ToolCatalogManager
from app.agent.tools.mcp import MCPTool
from app.agent.tools.static import StaticTool


def _static_tool_catalog() -> ToolCatalog:
    catalog = ToolCatalog()
    for function in builtin.STATIC_TOOL_FUNCTIONS.values():
        catalog.register(StaticTool(function))
    return catalog


STATIC_TOOL_CATALOG = _static_tool_catalog()


def build_tool_catalog(registry: dict | None = None) -> ToolCatalog:
    """Build the tools available for one agent turn from built-ins and MCP discovery."""
    if registry is None:
        from app.agent.tools.integrations.mcp_registry import get_mcp_registry

        registry = get_mcp_registry()

    catalog = _static_tool_catalog()
    servers = registry.get("servers", []) if isinstance(registry, dict) else []
    for server in servers:
        if not isinstance(server, dict) or not server.get("name") or not server.get("url"):
            continue
        definitions = server.get("tools", [])
        if not isinstance(definitions, list):
            continue
        for definition in definitions:
            if not isinstance(definition, dict) or not definition.get("name"):
                continue
            catalog.register(
                MCPTool(
                    server=str(server["name"]),
                    tool_name=str(definition["name"]),
                    url=str(server["url"]),
                    tool_description=str(definition.get("description") or ""),
                    schema=definition.get("inputSchema") or definition.get("input_schema") or {},
                )
            )
    return catalog


def render_tool_descriptions(catalog: ToolCatalog | None = None) -> str:
    """Render the planner's tool list from registered tool interfaces."""
    return (catalog or STATIC_TOOL_CATALOG).render_descriptions()


for _name in builtin.STATIC_TOOL_FUNCTIONS:
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


__all__ = [
    "Tool", "StaticTool", "MCPTool", "ToolCatalog", "ToolCatalogManager", "STATIC_TOOL_CATALOG",
    "build_tool_catalog", "render_tool_descriptions", *builtin.STATIC_TOOL_FUNCTIONS,
]
