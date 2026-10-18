import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.agent.agent import Agent
from app.agent.api.routes import router
from app.agent.config import get_api_settings
from app.agent.version import APP_VERSION
from app.agent.web.routes import STATIC_DIR

@asynccontextmanager
async def lifespan(app: FastAPI):
    agent = Agent(get_api_settings())
    app.state.agent = agent
    error = agent.startup()
    if error:
        logging.getLogger("rag-api").warning("Could not load agent memory: %s", error)
    await agent.tool_catalog_manager.start()
    try:
        yield
    finally:
        await agent.tool_catalog_manager.stop()


app = FastAPI(title="Nextcloud Personal RAG", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(router)
