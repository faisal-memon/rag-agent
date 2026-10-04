"""Read-only adapter for the private RSS news MCP server."""

from __future__ import annotations

import anyio

from app.agent.config import get_api_settings


def get_news(category: str = "technology", limit: int = 10) -> dict:
    """Fetch current RSS headlines from the news MCP server.

    Use for requests about current news, headlines, or articles. ``category`` may be
    a configured category such as technology, science, or hackernews.
    """
    try:
        return anyio.run(_get_news, category.strip() or "technology", max(1, min(limit, 50)))
    except Exception as exc:  # pragma: no cover - network and SDK failures
        return {"error": f"News MCP request failed: {exc}"}


async def _get_news(category: str, limit: int) -> dict:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(get_api_settings().news_mcp_url) as (read, write, _):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool("get_news", {"category": category, "limit": limit, "format": "json"})
            content = [item.text for item in result.content if getattr(item, "text", None) is not None]
            return {"content": content}
