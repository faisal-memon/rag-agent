import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from app.api.agent import answer_with_agent
from app.api.agent.memory import get_memory_store
from app.api.agent.prompts import initialize_prompts
from app.api.pipeline import pipeline_status
from app.api.retrieval import answer_question, retrieve_debug
from app.api.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    PipelineStatusResponse,
    QueryRequest,
    QueryResponse,
    ReindexResponse,
    RetrievalDebugResponse,
)
from app.api.web import STATIC_DIR, debug_page, index_page

@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_prompts()
    error = get_memory_store().load()
    if error:
        logging.getLogger("rag-api").warning("Could not load agent memory: %s", error)
    yield


app = FastAPI(title="Nextcloud Personal RAG", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return index_page()


@app.get("/debug", include_in_schema=False)
def debug_console():
    return debug_page()


@app.get("/healthz")
def healthcheck() -> dict:
    return {"status": "ok"}


@app.post("/reindex", response_model=ReindexResponse)
def reindex() -> ReindexResponse:
    from app.embed.service import reindex_source

    return ReindexResponse(**reindex_source())


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    return QueryResponse(**answer_question(request.question, mode=request.mode))


@app.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: AgentQueryRequest) -> AgentQueryResponse:
    history = [message.model_dump() for message in request.history]
    return AgentQueryResponse(**answer_with_agent(request.question, history=history))


@app.post("/debug/retrieve", response_model=RetrievalDebugResponse)
def debug_retrieve(request: QueryRequest) -> RetrievalDebugResponse:
    result = retrieve_debug(request.question, mode=request.mode, limit=request.limit, offset=request.offset)
    return RetrievalDebugResponse(
        question=request.question,
        mode=request.mode,
        **result,
    )


@app.get("/debug/pipeline", response_model=PipelineStatusResponse)
def debug_pipeline(limit: int = Query(default=10, ge=1, le=100)) -> PipelineStatusResponse:
    return PipelineStatusResponse(**pipeline_status(limit=limit))
