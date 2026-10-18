import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.agent.agent import Agent
from app.agent.api.routes import router
from app.agent.config import get_api_settings
from app.agent.tools.integrations.mcp_registry import MCP_REGISTRY_CACHE_TTL_SECONDS
from app.agent.version import APP_VERSION
from app.agent.web.routes import STATIC_DIR


async def _refresh_tool_catalog_periodically(app: FastAPI) -> None:
    """Keep MCP tools current while the API process is running."""
    while True:
        await asyncio.sleep(MCP_REGISTRY_CACHE_TTL_SECONDS)
        agent = app.state.agent
        try:
            await asyncio.to_thread(agent.refresh_tool_catalog)
        except Exception:
            logging.getLogger("rag-api").exception("Could not refresh MCP tool catalog")


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent = Agent(get_api_settings())
    app.state.agent = agent
    error = agent.startup()
    if error:
        logging.getLogger("rag-api").warning("Could not load agent memory: %s", error)
    await asyncio.to_thread(agent.refresh_tool_catalog)
    refresh_task = asyncio.create_task(_refresh_tool_catalog_periodically(app))
    try:
        yield
    finally:
        refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await refresh_task


app = FastAPI(title="Nextcloud Personal RAG", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(router)
