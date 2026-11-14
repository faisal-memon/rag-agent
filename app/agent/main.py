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
from app.agent.piper_service import PiperService
from app.agent.version import APP_VERSION
from app.agent.web.routes import STATIC_DIR


def main() -> FastAPI:
    """Configure process logging and construct the application."""
    _configure_logging()
    return create_app()


def _configure_logging() -> None:
    """Give app and Uvicorn logs one consistent Pacific-time timestamp."""
    app_logger = logging.getLogger("app.agent")
    app_logger.setLevel(logging.INFO)
    app_logger.handlers.clear()
    app_logger.addHandler(_pacific_log_handler())
    app_logger.propagate = False

    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.setLevel(logging.INFO)
    uvicorn_logger.handlers.clear()
    uvicorn_logger.addHandler(_pacific_log_handler())
    uvicorn_logger.propagate = False
    for logger_name in ("uvicorn.error", "uvicorn.access"):
        child_logger = logging.getLogger(logger_name)
        child_logger.handlers.clear()
        child_logger.setLevel(logging.INFO)
        child_logger.propagate = True


def _pacific_log_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %Z",
    )
    formatter.converter = time.localtime
    handler.setFormatter(formatter)
    return handler


def create_app() -> FastAPI:
    """Build the FastAPI application and attach its routes and middleware."""
    application = FastAPI(
        title="Nextcloud Personal RAG",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    application.include_router(router)
    application.middleware("http")(revalidate_static_assets)
    return application


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The catalog manager owns MCP discovery and its background refresh task.
    settings = get_api_settings()
    tool_catalog_manager = tools.ToolCatalogManager(settings.mcp_registry_refresh_interval)
    await tool_catalog_manager.start()

    # Requests share this agent and its initialized tool catalog manager.
    agent = Agent(settings, tool_catalog_manager)
    app.state.agent = agent
    if error := agent.startup():
        logging.getLogger("app.agent").warning("Could not load agent memory: %s", error)

    # Piper configuration is shared by the browser and ESPHome speaker paths.
    piper_service = PiperService(settings)
    app.state.piper_service = piper_service

    # Setup ESP Home Client
    esphome_voice_client = ESPHomeVoiceClient(settings, agent, piper_service)
    app.state.esphome_voice_client = esphome_voice_client
    await esphome_voice_client.start()

    try:
        yield
    finally:
        await esphome_voice_client.stop()
        await tool_catalog_manager.stop()


async def revalidate_static_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app = main()
