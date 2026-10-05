"""Read the approved MCP server registry."""

from __future__ import annotations

import json
import anyio
from urllib.request import Request, urlopen

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.agent.config import get_api_settings


class MCPServer(BaseModel):
    """One approved MCP endpoint advertised by the registry."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    url: AnyHttpUrl
    description: str | None = None
    enabled: bool = True
    tools: list[dict] | dict | None = None

    def as_tool_entry(self) -> dict[str, object]:
        """Return the stable, JSON-compatible shape exposed to the agent."""
        return self.model_dump(mode="json", exclude_none=True)


class MCPRegistry(BaseModel):
    """Validated registry document returned by the registry service."""

    model_config = ConfigDict(extra="ignore")

    servers: list[MCPServer]


def get_mcp_registry() -> dict:
    """List approved MCP servers from the configured registry URL."""
    try:
        request = Request(get_api_settings().mcp_registry_url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
        registry = MCPRegistry.model_validate(payload)
        servers = []
        for server in registry.servers:
            if server.enabled:
                entry = server.as_tool_entry()
                entry["tools"] = _list_tools(str(server.url))
                servers.append(entry)
        return {"servers": servers}
    except (TypeError, ValueError):
        return {"error": "MCP registry returned an invalid document."}
    except Exception as exc:  # pragma: no cover - network failures
        return {"error": f"MCP registry request failed: {exc}"}


async def _list_tools_async(url: str) -> list[dict]:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return [tool.model_dump(mode="json") for tool in result.tools]


async def _list_tools_with_timeout(url: str) -> list[dict]:
    with anyio.fail_after(5):
        return await _list_tools_async(url)


def _list_tools(url: str) -> list[dict] | dict:
    if not url:
        return {"error": "MCP server has no URL."}
    try:
        return anyio.run(_list_tools_with_timeout, url)
    except Exception as exc:  # pragma: no cover - network failures
        return {"error": f"MCP tool discovery failed: {exc}"}


async def _call_tool_async(url: str, name: str, arguments: dict) -> object:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            return result.model_dump(mode="json")


async def _call_tool_with_timeout(url: str, name: str, arguments: dict) -> object:
    with anyio.fail_after(10):
        return await _call_tool_async(url, name, arguments)


def call_mcp_tool(server: str, tool: str, arguments: dict | None = None) -> dict:
    """Call an approved MCP tool by registry server name after discovery.

    Args:
        server: The registry name of the approved MCP server.
        tool: The exact tool name returned by get_mcp_registry.
        arguments: JSON arguments matching the discovered tool schema.
    """
    registry = get_mcp_registry()
    for entry in registry.get("servers", []):
        if entry.get("name") == server:
            try:
                result = anyio.run(_call_tool_with_timeout, str(entry["url"]), tool, arguments or {})
                return {"server": server, "tool": tool, "result": result}
            except Exception as exc:  # pragma: no cover - network failures
                return {"server": server, "tool": tool, "error": f"MCP tool call failed: {exc}"}
    return {"error": f"Unknown or disabled MCP server: {server}"}
