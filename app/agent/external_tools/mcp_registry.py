"""Read the approved MCP server registry."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from app.agent.config import get_api_settings


def get_mcp_registry() -> dict:
    """List approved MCP servers from the configured registry URL."""
    try:
        request = Request(get_api_settings().mcp_registry_url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
            return {"error": "MCP registry returned an invalid document."}
        return {"servers": [item for item in payload["servers"] if isinstance(item, dict) and item.get("enabled", True)]}
    except Exception as exc:  # pragma: no cover - network failures
        return {"error": f"MCP registry request failed: {exc}"}
