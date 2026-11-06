"""HTTP routes that adapt FastAPI requests to the agent runtime."""

import json
import logging
from queue import Empty, Queue
from threading import Thread
from typing import Iterator

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.agent import tools, voice_devices
from app.agent.agent import Agent
from app.agent.config import get_api_settings
from app.embed.config import get_embed_settings
from app.agent.conversations import append_messages, delete_conversation, get_conversation, list_conversations, load_or_create
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
from app.agent.web.routes import debug_page, devices_page, index_page, settings_page, tools_page
from app.agent.transcription import TranscriptionUnavailableError, transcribe_audio
from app.agent.prompts import render_prompt
from app.core.config import read_runtime_settings, write_runtime_settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _agent_exception_message(exc: Exception) -> str:
    """Preserve the useful exception identity when streaming an agent failure."""
    parts = [f"{type(exc).__name__}: {exc}" or type(exc).__name__]
    cause = exc.__cause__ or exc.__context__
    if cause:
        parts.append(f"caused by {type(cause).__name__}: {cause}")
    return " | ".join(parts)


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
    system_prompt_is_default = settings.system_prompt is None
    default_system_prompt = render_prompt("system.md", {})
    return AgentRuntimeSettings(
        llm_provider=settings.llm_provider,
        openai_chat_model=settings.openai_chat_model,
        llamacpp_base_url=settings.llamacpp_base_url,
        llamacpp_chat_model=settings.llamacpp_chat_model,
        embedding_provider=get_embed_settings().provider,
        openai_embedding_model=get_embed_settings().openai_embedding_model,
        embedding_llamacpp_base_url=get_embed_settings().llamacpp_base_url,
        embedding_llamacpp_model=get_embed_settings().llamacpp_model,
        query_limit=settings.query_limit,
        agent_max_steps=settings.agent_max_steps,
        system_prompt=settings.system_prompt or default_system_prompt,
        default_system_prompt=default_system_prompt,
        system_prompt_is_default=system_prompt_is_default,
        home_latitude=settings.home_latitude,
        home_longitude=settings.home_longitude,
        memory_enabled=settings.memory_enabled,
        memory_require_approval=settings.memory_require_approval,
        voice_start_grace_ms=settings.voice_start_grace_ms,
        voice_end_silence_ms=settings.voice_end_silence_ms,
        voice_vad_window_frames=settings.voice_vad_window_frames,
        voice_vad_window_speech_frames=settings.voice_vad_window_speech_frames,
        voice_vad_activity_history=settings.voice_vad_activity_history,
        voice_vad_active_windows_required=settings.voice_vad_active_windows_required,
        voice_capture_limit_seconds=settings.voice_capture_limit_seconds,
        remote_mcp_servers=settings.remote_mcp_servers,
        voice_follow_up_timeout_seconds=settings.voice_follow_up_timeout_seconds,
        voice_follow_up_start_timeout_seconds=settings.voice_follow_up_start_timeout_seconds,
    )


async def _reload_agent(request: Request) -> None:
    get_api_settings.cache_clear()
    get_embed_settings.cache_clear()
    settings = get_api_settings()
    tool_catalog_manager = tools.ToolCatalogManager(settings.mcp_registry_refresh_interval)
    await tool_catalog_manager.start()
    agent = Agent(settings, tool_catalog_manager)
    agent.startup()
    voice_client = getattr(request.app.state, "esphome_voice_client", None)
    if voice_client is not None:
        voice_client.update_settings(settings)
    previous_agent = getattr(request.app.state, "agent", None)
    request.app.state.agent = agent
    if previous_agent is not None:
        await previous_agent.tool_catalog_manager.stop()


@router.get("/", include_in_schema=False)
def index():
    return index_page()


@router.get("/debug", include_in_schema=False)
def debug_console():
    return debug_page()


@router.get("/settings", include_in_schema=False)
def settings_console():
    return settings_page()


@router.get("/tools", include_in_schema=False)
def tools_console(request: Request):
    _conversation_user(request)
    return tools_page()


@router.get("/devices", include_in_schema=False)
def devices_console(request: Request):
    _conversation_user(request)
    return devices_page()


@router.get("/api/devices")
def list_voice_devices(request: Request) -> list[dict]:
    _conversation_user(request)
    devices = voice_devices.list_devices()
    voice_client = getattr(request.app.state, "esphome_voice_client", None)
    live_status = (
        voice_client.status()
        if voice_client is not None
        else {"device_id": None, "connected": False, "host": "", "port": 6053}
    )
    live_id = live_status.get("device_id")
    found_live_device = False
    for device in devices:
        device["connected"] = bool(live_status["connected"] and device["device_id"] == live_id)
        found_live_device = found_live_device or device["device_id"] == live_id
    if live_id and not found_live_device:
        devices.append(
            {
                **live_status,
                "first_seen_at": None,
                "last_connected_at": None,
                "last_interaction_at": None,
            }
        )
    elif not live_id and not devices and live_status.get("host"):
        devices.append(
            {
                **live_status,
                "device_id": "not-yet-seen",
                "first_seen_at": None,
                "last_connected_at": None,
                "last_interaction_at": None,
            }
        )
    return devices


@router.get("/api/tools")
def list_mcp_tools(request: Request) -> dict:
    """Return the latest discovered MCP servers and their tool availability."""
    _conversation_user(request)
    manager = _agent(request).tool_catalog_manager
    registry = manager.registry_snapshot()
    servers = []
    for server in registry.get("servers", []):
        tools_result = server.get("tools")
        reachable = isinstance(tools_result, list)
        servers.append({
            "name": server.get("name", "Unnamed server"),
            "description": server.get("description"),
            "registered": server.get("source", "private") == "private",
            "source": server.get("source", "private"),
            "reachable": reachable,
            "error": tools_result.get("error") if isinstance(tools_result, dict) else None,
            "tools": tools_result if reachable else [],
        })
    return {"servers": servers, "refresh_error": registry.get("error")}


@router.get("/api/devices/{device_id}/history")
def voice_device_history(
    request: Request,
    device_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    _conversation_user(request)
    history = voice_devices.get_history(device_id, limit)
    if history is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return history


@router.get("/api/devices/{device_id}/activity")
def voice_device_activity(
    request: Request,
    device_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    _conversation_user(request)
    events = voice_devices.get_activity(device_id, limit)
    if events is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return events


@router.get("/healthz")
def healthcheck() -> dict:
    return {"status": "ok"}


@router.get("/api/settings", response_model=AgentRuntimeSettings)
def get_settings() -> AgentRuntimeSettings:
    return _runtime_settings()


@router.put("/api/settings", response_model=AgentRuntimeSettings)
async def update_settings(request: Request, payload: AgentRuntimeSettings) -> AgentRuntimeSettings:
    settings = read_runtime_settings()
    values = payload.model_dump(mode="json")
    system_prompt_is_default = values.pop("system_prompt_is_default")
    values.pop("default_system_prompt")
    embed_values = {
        "provider": values.pop("embedding_provider"),
        "openai_embedding_model": values.pop("openai_embedding_model"),
        "llamacpp_base_url": values.pop("embedding_llamacpp_base_url"),
        "llamacpp_model": values.pop("embedding_llamacpp_model"),
    }
    api_settings = {**settings.get("api", {}), **values}
    if system_prompt_is_default:
        api_settings.pop("system_prompt", None)
    settings["api"] = api_settings
    settings["embed"] = {**settings.get("embed", {}), **embed_values}
    write_runtime_settings(settings)
    await _reload_agent(request)
    return _runtime_settings()


@router.post("/reindex", response_model=ReindexResponse)
def reindex() -> ReindexResponse:
    from app.embed.service import reindex_source

    return ReindexResponse(**reindex_source())


@router.post("/agent/query", response_model=AgentQueryResponse)
def agent_query(request: Request, payload: AgentQueryRequest) -> AgentQueryResponse:
    conversation_id, saved_history, user = _load_conversation(request, payload)
    history = saved_history
    profile_name = _conversation_profile_name(request)
    answer_kwargs = {"profile_name": profile_name} if profile_name else {}
    if payload.image_data_url:
        answer_kwargs["image_data_url"] = payload.image_data_url
    result = _agent(request).answer(payload.question, history=history, **answer_kwargs)
    saved_question = payload.question + ("\n[Image attached for this turn; image not saved]" if payload.image_data_url else "")
    append_messages(user, conversation_id, [{"role": "user", "content": saved_question}, {"role": "assistant", "content": result["answer"]}])
    return AgentQueryResponse(conversation_id=conversation_id, **result)

@router.get("/agent/conversations")
def conversations(request: Request) -> list[dict]:
    return list_conversations(_conversation_user(request))

@router.get("/agent/conversations/{conversation_id}")
def conversation(request: Request, conversation_id: str) -> list[dict]:
    messages = get_conversation(_conversation_user(request), conversation_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return messages

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
    history = saved_history
    agent = _agent(request)
    events: Queue[dict | None] = Queue()

    def run_agent() -> None:
        try:
            profile_name = _conversation_profile_name(request)
            answer_kwargs = {"profile_name": profile_name} if profile_name else {}
            if payload.image_data_url:
                answer_kwargs["image_data_url"] = payload.image_data_url
            result = agent.answer(payload.question, history=history, on_progress=events.put, **answer_kwargs)
            saved_question = payload.question + ("\n[Image attached for this turn; image not saved]" if payload.image_data_url else "")
            append_messages(user, conversation_id, [{"role": "user", "content": saved_question}, {"role": "assistant", "content": result["answer"]}])
            result["conversation_id"] = conversation_id
            events.put({"type": "complete", "result": result})
        except Exception as exc:  # pragma: no cover - exercised by the browser error path
            logger.exception("Agent request failed", extra={"conversation_id": conversation_id})
            events.put({
                "type": "error",
                "message": _agent_exception_message(exc),
                "error_type": type(exc).__name__,
            })
        finally:
            events.put(None)

    def event_stream() -> Iterator[str]:
        worker = Thread(target=run_agent, daemon=True)
        worker.start()
        while True:
            try:
                event = events.get(timeout=5)
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
