"""HTTP routes that adapt FastAPI requests to the agent runtime."""

import json
from queue import Empty, Queue
from threading import Thread
from typing import Iterator

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.agent.agent import Agent
from app.agent.config import get_api_settings
from app.agent.conversations import append_messages, delete_conversation, list_conversations, load_or_create
from app.agent.api.schemas import (
    AgentRuntimeSettings,
    AgentQueryRequest,
    AgentQueryResponse,
    PipelineStatusResponse,
    QueryRequest,
    ReindexResponse,
    RetrievalDebugResponse,
    TranscriptResponse,
)
from app.agent.pipeline import pipeline_status
from app.agent.search import search_debug
from app.agent.web.routes import debug_page, index_page, settings_page
from app.agent.transcription import TranscriptionUnavailableError, transcribe_audio
from app.core.config import read_runtime_settings, write_runtime_settings

router = APIRouter()


def _agent(request: Request) -> Agent:
    return request.app.state.agent


def _conversation_user(request: Request) -> str:
    user = str(request.headers.get("Remote-User", "")).strip()
    if not user:
        raise HTTPException(status_code=401, detail="Sign in through Authelia before using conversation history.")
    return user


def _conversation_profile_name(request: Request) -> str | None:
    name = str(request.headers.get("Remote-Name", "")).strip()
    return name or None


def _load_conversation(request: Request, payload: AgentQueryRequest) -> tuple[str, list[dict], str]:
    user = _conversation_user(request)
    try:
        conversation_id, saved_history = load_or_create(user, payload.conversation_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return conversation_id, saved_history, user


def _runtime_settings() -> AgentRuntimeSettings:
    settings = get_api_settings()
    return AgentRuntimeSettings(
        llm_provider=settings.llm_provider,
        openai_chat_model=settings.openai_chat_model,
        llamacpp_base_url=settings.llamacpp_base_url,
        llamacpp_chat_model=settings.llamacpp_chat_model,
        query_limit=settings.query_limit,
        agent_max_steps=settings.agent_max_steps,
    )


def _reload_agent(request: Request) -> None:
    get_api_settings.cache_clear()
    agent = Agent(get_api_settings())
    request.app.state.agent = agent
    agent.startup()


@router.get("/", include_in_schema=False)
def index():
    return index_page()


@router.get("/debug", include_in_schema=False)
def debug_console():
    return debug_page()


@router.get("/settings", include_in_schema=False)
def settings_console():
    return settings_page()


@router.get("/healthz")
def healthcheck() -> dict:
    return {"status": "ok"}


@router.get("/api/settings", response_model=AgentRuntimeSettings)
def get_settings() -> AgentRuntimeSettings:
    return _runtime_settings()


@router.put("/api/settings", response_model=AgentRuntimeSettings)
def update_settings(request: Request, payload: AgentRuntimeSettings) -> AgentRuntimeSettings:
    settings = read_runtime_settings()
    settings["api"] = {**settings.get("api", {}), **payload.model_dump()}
    write_runtime_settings(settings)
    _reload_agent(request)
    return _runtime_settings()


@router.post("/reindex", response_model=ReindexResponse)
def reindex() -> ReindexResponse:
    from app.embed.service import reindex_source

    return ReindexResponse(**reindex_source())


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: Request, payload: AgentQueryRequest) -> AgentQueryResponse:
    conversation_id, saved_history, user = _load_conversation(request, payload)
    history = saved_history or [message.model_dump() for message in payload.history]
    profile_name = _conversation_profile_name(request)
    answer_kwargs = {"profile_name": profile_name} if profile_name else {}
    result = _agent(request).answer(payload.question, history=history, **answer_kwargs)
    append_messages(user, conversation_id, [{"role": "user", "content": payload.question}, {"role": "assistant", "content": result["answer"]}])
    return AgentQueryResponse(conversation_id=conversation_id, **result)

@router.get("/agent/conversations")
def conversations(request: Request) -> list[dict]:
    return list_conversations(_conversation_user(request))

@router.delete("/agent/conversations/{conversation_id}", status_code=204)
def remove_conversation(request: Request, conversation_id: str) -> None:
    if not delete_conversation(_conversation_user(request), conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.post("/agent/transcribe", response_model=TranscriptResponse)
async def transcribe(request: Request, audio: UploadFile = File(...)) -> TranscriptResponse:
    """Transcribe a short browser recording without sending it to an external service."""
    if not (audio.content_type or "").startswith(("audio/", "video/")):
        raise HTTPException(status_code=415, detail="Record an audio clip before transcribing.")
    content = await audio.read(20 * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The recording was empty.")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Keep recordings under 20 MB.")
    try:
        text = await run_in_threadpool(
            transcribe_audio,
            base_url=get_api_settings().whispercpp_base_url,
            audio=content,
            filename=audio.filename or "recording.webm",
            content_type=audio.content_type or "audio/webm",
        )
    except TranscriptionUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return TranscriptResponse(text=text)


@router.post("/agent/query/stream")
def stream_agent_query(request: Request, payload: AgentQueryRequest) -> StreamingResponse:
    """Stream concise agent lifecycle events, followed by the final response."""
    conversation_id, saved_history, user = _load_conversation(request, payload)
    history = saved_history or [message.model_dump() for message in payload.history]
    agent = _agent(request)
    events: Queue[dict | None] = Queue()

    def run_agent() -> None:
        try:
            profile_name = _conversation_profile_name(request)
            answer_kwargs = {"profile_name": profile_name} if profile_name else {}
            result = agent.answer(payload.question, history=history, on_progress=events.put, **answer_kwargs)
            append_messages(user, conversation_id, [{"role": "user", "content": payload.question}, {"role": "assistant", "content": result["answer"]}])
            result["conversation_id"] = conversation_id
            events.put({"type": "complete", "result": result})
        except Exception as exc:  # pragma: no cover - exercised by the browser error path
            events.put({"type": "error", "message": str(exc)})
        finally:
            events.put(None)

    def event_stream() -> Iterator[str]:
        worker = Thread(target=run_agent, daemon=True)
        worker.start()
        while True:
            try:
                event = events.get(timeout=15)
            except Empty:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/debug/retrieve", response_model=RetrievalDebugResponse)
def debug_retrieve(payload: QueryRequest) -> RetrievalDebugResponse:
    result = search_debug(payload.question, mode=payload.mode, limit=payload.limit, offset=payload.offset)
    return RetrievalDebugResponse(question=payload.question, mode=payload.mode, **result)


@router.get("/debug/pipeline", response_model=PipelineStatusResponse)
def debug_pipeline(limit: int = Query(default=10, ge=1, le=100)) -> PipelineStatusResponse:
    return PipelineStatusResponse(**pipeline_status(limit=limit))
