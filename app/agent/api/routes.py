"""HTTP routes that adapt FastAPI requests to the agent runtime."""

from fastapi import APIRouter, Query, Request

from app.agent.agent import Agent
from app.agent.api.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    PipelineStatusResponse,
    QueryRequest,
    ReindexResponse,
    RetrievalDebugResponse,
)
from app.agent.pipeline import pipeline_status
from app.agent.search import search_debug
from app.agent.web.routes import debug_page, index_page

router = APIRouter()


def _agent(request: Request) -> Agent:
    return request.app.state.agent


@router.get("/", include_in_schema=False)
def index():
    return index_page()


@router.get("/debug", include_in_schema=False)
def debug_console():
    return debug_page()


@router.get("/healthz")
def healthcheck() -> dict:
    return {"status": "ok"}


@router.post("/reindex", response_model=ReindexResponse)
def reindex() -> ReindexResponse:
    from app.embed.service import reindex_source

    return ReindexResponse(**reindex_source())


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: Request, payload: AgentQueryRequest) -> AgentQueryResponse:
    history = [message.model_dump() for message in payload.history]
    result = _agent(request).answer(payload.question, history=history)
    return AgentQueryResponse(**result)


@router.post("/debug/retrieve", response_model=RetrievalDebugResponse)
def debug_retrieve(payload: QueryRequest) -> RetrievalDebugResponse:
    result = search_debug(payload.question, mode=payload.mode, limit=payload.limit, offset=payload.offset)
    return RetrievalDebugResponse(question=payload.question, mode=payload.mode, **result)


@router.get("/debug/pipeline", response_model=PipelineStatusResponse)
def debug_pipeline(limit: int = Query(default=10, ge=1, le=100)) -> PipelineStatusResponse:
    return PipelineStatusResponse(**pipeline_status(limit=limit))
