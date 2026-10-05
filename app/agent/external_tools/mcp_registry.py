"""Read the approved MCP server registry."""

from __future__ import annotations

import json
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
        return {"servers": [server.as_tool_entry() for server in registry.servers if server.enabled]}
    except (TypeError, ValueError):
        return {"error": "MCP registry returned an invalid document."}
    except Exception as exc:  # pragma: no cover - network failures
        return {"error": f"MCP registry request failed: {exc}"}
