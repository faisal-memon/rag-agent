import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.agent.api.routes import router
from app.agent.config import get_api_settings
from app.agent.runtime import AgentRuntime
from app.agent.web.routes import STATIC_DIR

@asynccontextmanager
async def lifespan(_app: FastAPI):
    runtime = AgentRuntime(get_api_settings())
    _app.state.agent_runtime = runtime
    error = runtime.startup()
    if error:
        logging.getLogger("rag-api").warning("Could not load agent memory: %s", error)
    yield


app = FastAPI(title="Nextcloud Personal RAG", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(router)
