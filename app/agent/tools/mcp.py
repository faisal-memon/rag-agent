"""Adapter for a discovered tool hosted by an MCP server."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.tools.base import Tool


@dataclass(frozen=True)
class MCPTool(Tool):
    """A remote tool discovered from an approved MCP server."""

    server: str
    tool_name: str
    url: str = field(repr=False)
    tool_description: str
    schema: dict[str, Any]

    @property
    def name(self) -> str:
        return f"{self.server}/{self.tool_name}"

    @property
    def description(self) -> str:
        return self.tool_description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.schema

    def _execute(self, arguments: dict[str, Any]) -> Any:
        import anyio
        from app.agent.tools.integrations.mcp_registry import _call_tool_with_timeout

        result = anyio.run(_call_tool_with_timeout, self.url, self.tool_name, arguments)
        if not isinstance(result, dict) or not result.get("isError"):
            return result
        content = result.get("content") or []
        error = "; ".join(
            str(item.get("text")) for item in content if isinstance(item, dict) and item.get("text")
        ) or "MCP server reported a tool error."
        return {"error": error}
