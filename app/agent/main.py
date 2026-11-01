import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.agent import tools
from app.agent.agent import Agent
from app.agent.api.routes import router
from app.agent.config import get_api_settings
from app.agent.esphome_voice import ESPHomeVoiceClient
from app.agent.version import APP_VERSION
from app.agent.web.routes import STATIC_DIR

def _pacific_log_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %Z",
    )
    formatter.converter = time.localtime
    handler.setFormatter(formatter)
    return handler


# Keep application diagnostics and Uvicorn records on one Pacific-time format.
for logger_name in (
    "app.agent.agent",
    "app.agent.esphome_voice",
    "app.agent.esphome_playback",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
):
    configured_logger = logging.getLogger(logger_name)
    configured_logger.setLevel(logging.INFO)
    configured_logger.handlers.clear()
    configured_logger.addHandler(_pacific_log_handler())
    configured_logger.propagate = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # The catalog manager owns MCP discovery and its background refresh task.
    settings = get_api_settings()
    tool_catalog_manager = tools.ToolCatalogManager(settings.mcp_registry_refresh_interval)
    await tool_catalog_manager.start()
    # Requests share this agent and its initialized tool catalog manager.
    agent = Agent(settings, tool_catalog_manager)
    app.state.agent = agent
    error = agent.startup()
    if error:
        logging.getLogger("rag-api").warning("Could not load agent memory: %s", error)
    esphome_voice_client = ESPHomeVoiceClient(settings, agent)
    await esphome_voice_client.start()
    try:
        yield
    finally:
        await esphome_voice_client.stop()
        await tool_catalog_manager.stop()


app = FastAPI(title="Nextcloud Personal RAG", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(router)


@app.middleware("http")
async def revalidate_static_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
