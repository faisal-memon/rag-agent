"""Registry and lookup for the tools exposed to the planner."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from threading import Lock
from typing import Any, Callable

from app.agent.tools.base import Tool, ToolExecutionContext

logger = logging.getLogger(__name__)


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

    def execute(
        self, name: str, arguments: dict[str, Any], context: ToolExecutionContext | None = None
    ) -> Any:
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool.execute(arguments, context)

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


class ToolCatalogManager:
    """Own the current tool catalog and its asynchronous refresh lifecycle."""

    def __init__(
        self,
        refresh_interval: float,
        registry_loader: Callable[[], dict] | None = None,
        catalog_builder: Callable[[dict], ToolCatalog] | None = None,
    ) -> None:
        self._refresh_interval = refresh_interval
        self._registry_loader = registry_loader
        self._catalog_builder = catalog_builder
        from app.agent.tools import STATIC_TOOL_CATALOG

        self._catalog = STATIC_TOOL_CATALOG
        self._registry: dict | None = None
        self._catalog_lock = Lock()
        self._refresh_lock = Lock()
        self._refresh_task: asyncio.Task | None = None

    def current(self) -> ToolCatalog:
        """Return the current catalog snapshot."""
        with self._catalog_lock:
            return self._catalog

    def refresh(self) -> ToolCatalog:
        """Fetch MCP discovery and replace the catalog only when it changes."""
        registry_loader = self._registry_loader
        if registry_loader is None:
            from app.agent.tools.integrations.mcp_registry import get_mcp_registry

            registry_loader = get_mcp_registry
        catalog_builder = self._catalog_builder
        if catalog_builder is None:
            from app.agent.tools import build_tool_catalog

            catalog_builder = build_tool_catalog

        with self._refresh_lock:
            try:
                registry = registry_loader()
            except Exception:
                logger.exception("Could not refresh MCP tool catalog")
                return self.current()
            if registry.get("error"):
                logger.warning("Could not refresh MCP tool catalog: %s", registry["error"])
                return self.current()
            with self._catalog_lock:
                if registry != self._registry:
                    try:
                        self._catalog = catalog_builder(registry)
                    except Exception:
                        logger.exception("Could not build MCP tool catalog")
                        return self._catalog
                    self._registry = registry
                return self._catalog

    async def start(self) -> ToolCatalog:
        """Load once, then keep the catalog current until :meth:`stop` is called."""
        if self._refresh_task is not None and not self._refresh_task.done():
            return self.current()
        await asyncio.to_thread(self.refresh)
        self._refresh_task = asyncio.create_task(self._refresh_periodically())
        return self.current()

    async def stop(self) -> None:
        """Cancel and await the periodic refresh task."""
        task = self._refresh_task
        self._refresh_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _refresh_periodically(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            try:
                await asyncio.to_thread(self.refresh)
            except Exception:
                logger.exception("Could not refresh MCP tool catalog")
