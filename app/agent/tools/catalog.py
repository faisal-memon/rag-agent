"""Registry and lookup for the tools exposed to the planner."""

from __future__ import annotations

from typing import Any

from app.agent.tools.base import Tool


class ToolCatalog:
    """Owns tool registration, stable names, and dispatch lookup."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool is already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def with_capability(self, capability: str) -> tuple[Tool, ...]:
        return tuple(tool for tool in self.tools if capability in tool.capabilities)

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool.execute(arguments)

    def render_descriptions(self) -> str:
        """Build the planner's tool list from the registered tool interfaces."""
        descriptions: list[str] = []
        for tool in self.tools:
            descriptions.append(f"- {tool.name}: {tool.description}")
            descriptions.append("  Arguments:")
            for name, schema in tool.input_schema.get("properties", {}).items():
                descriptions.append(f"  - {name}: {schema.get('description') or 'No description provided.'}")
        return "\n".join(descriptions)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(self._tools.values())
