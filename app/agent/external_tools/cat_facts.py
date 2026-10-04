"""Small read-only adapter for the public Cat Facts MCP demo server."""

from __future__ import annotations

import anyio

from app.agent.config import get_api_settings


def get_cat_fact() -> dict:
    """Return a random cat fact for lightweight cat-trivia requests.

    Use this when the user asks for a random cat fact, cat trivia, or a fun fact
    about cats. Do not use it for veterinary advice, behavior questions, or
    research requests that need evidence beyond a single trivia fact.

    The endpoint is fixed by application configuration and this adapter exposes
    only the server's read-only ``get_cat_fact`` tool to the planner.
    """

    try:
        return anyio.run(_get_cat_fact)
    except Exception as exc:  # pragma: no cover - network and SDK failures
        return {"error": f"Cat Facts MCP request failed: {exc}"}


async def _get_cat_fact() -> dict:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(get_api_settings().cat_facts_mcp_url) as (read, write, _):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool("get_cat_fact", {})
            content = []
            for item in result.content:
                text = getattr(item, "text", None)
                if text is not None:
                    content.append(text)
            return {"content": content}
