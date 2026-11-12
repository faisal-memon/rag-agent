from datetime import datetime
from typing import Any, Literal
import base64
import binascii
import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.agent.config import RemoteMCPServerSetting


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    mode: Literal["semantic", "keyword"] = "semantic"
    limit: int | None = Field(default=None, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Citation(BaseModel):
    chunk_id: int | None = None
    filename: str
    path: str
    section: str | None = None
    page: int | None = None
    content: str
    score: float
    fts_score: float | None = None
    vector_score: float | None = None
    matched_fts: bool | None = None
    matched_vector: bool | None = None
    retrieval_mode: str | None = None


class AgentQueryRequest(BaseModel):
    conversation_id: str | None = None
    question: str = Field(min_length=1)
    image_data_url: str | None = Field(default=None, max_length=7_000_000)

    @field_validator("image_data_url")
    @classmethod
    def validate_image_data_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/]+={0,2})", value)
        if not match:
            raise ValueError("Attach one PNG or JPEG image.")
        try:
            image = base64.b64decode(match.group(2), validate=True)
        except binascii.Error as exc:
            raise ValueError("The image could not be read.") from exc
        if not image or len(image) > 5 * 1024 * 1024:
            raise ValueError("Keep images under 5 MB.")
        if match.group(1) == "png" and not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("The selected file is not a PNG image.")
        if match.group(1) == "jpeg" and not image.startswith(b"\xff\xd8\xff"):
            raise ValueError("The selected file is not a JPEG image.")
        return value


class TranscriptResponse(BaseModel):
    text: str


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)


class AgentToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any]


class AgentToolResult(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result: Any


class AgentReasoningStep(BaseModel):
    phase: str
    content: str = Field(max_length=12000)


class AgentQueryResponse(BaseModel):
    conversation_id: str | None = None
    answer: str
    plan: list[AgentToolCall]
    tool_results: list[AgentToolResult]
    reasoning: list[AgentReasoningStep]
    debug: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[Citation]

class ConversationSummary(BaseModel):
    id: str
    created_at: str
    updated_at: str
    title: str


class AgentRuntimeSettings(BaseModel):
    llm_provider: Literal["openai", "llamacpp"]
    openai_chat_model: str = Field(min_length=1)
    llamacpp_base_url: str = Field(min_length=1)
    llamacpp_chat_model: str = Field(min_length=1)
    embedding_provider: Literal["openai", "llamacpp"]
    openai_embedding_model: str = Field(min_length=1)
    embedding_llamacpp_base_url: str = Field(min_length=1)
    embedding_llamacpp_model: str = Field(min_length=1)
    query_limit: int = Field(ge=1, le=100)
    agent_max_steps: int = Field(ge=1, le=12)
    system_prompt: str = Field(default="", max_length=20_000)
    default_system_prompt: str = Field(default="", max_length=20_000)
    system_prompt_is_default: bool = True
    home_latitude: float | None = Field(default=None, ge=-90, le=90)
    home_longitude: float | None = Field(default=None, ge=-180, le=180)
    memory_enabled: bool = True
    memory_require_approval: bool = True
    voice_start_grace_ms: int = Field(default=960, ge=0, le=10_000, multiple_of=20)
    voice_end_silence_ms: int = Field(default=800, ge=20, le=10_000, multiple_of=20)
    voice_vad_window_frames: int = Field(default=5, ge=1, le=20)
    voice_vad_window_speech_frames: int = Field(default=2, ge=1, le=20)
    voice_vad_activity_history: int = Field(default=3, ge=1, le=10)
    voice_vad_active_windows_required: int = Field(default=2, ge=1, le=10)
    voice_capture_limit_seconds: int = Field(default=20, ge=0, le=3600)
    remote_mcp_servers: list[RemoteMCPServerSetting] = Field(default_factory=list, max_length=20)

    @field_validator("remote_mcp_servers")
    @classmethod
    def remote_server_names_are_unique(
        cls, servers: list[RemoteMCPServerSetting]
    ) -> list[RemoteMCPServerSetting]:
        names = [server.name.casefold() for server in servers]
        if len(names) != len(set(names)):
            raise ValueError("Remote MCP server names must be unique.")
        return servers
    voice_follow_up_timeout_seconds: int = Field(default=8, ge=0, le=60)
    voice_follow_up_start_timeout_seconds: int = Field(default=3, ge=0, le=30)

    @model_validator(mode="after")
    def validate_vad_windows(self) -> "AgentRuntimeSettings":
        if not self.system_prompt_is_default and not self.system_prompt.strip():
            raise ValueError("System prompt cannot be empty unless it is reset to the default.")
        if self.voice_vad_window_speech_frames > self.voice_vad_window_frames:
            raise ValueError("Speech frames cannot exceed frames per window")
        if self.voice_vad_active_windows_required > self.voice_vad_activity_history:
            raise ValueError("Active windows cannot exceed recent windows")
        return self


class RetrievalDebugResponse(BaseModel):
    question: str
    mode: str
    limit: int
    offset: int
    total_chunks: int
    total_documents: int
    chunks: list[Citation]


class PipelineDocument(BaseModel):
    id: int
    path: str
    filename: str
    mime_type: str
    size_bytes: int
    modified_time: datetime
    last_indexed_at: datetime | None = None
    missing_since: datetime | None = None
    indexing_version: str
    embedding_model: str
    chunk_count: int


class PipelineIndexingStrategy(BaseModel):
    indexing_version: str
    embedding_model: str
    embedding_tokenizer: str
    chunk_size: int
    chunk_overlap: int
    document_count: int


class PipelineStatusResponse(BaseModel):
    document_count: int
    chunk_count: int
    missing_document_count: int
    recent_documents: list[PipelineDocument]
    missing_documents: list[PipelineDocument]
    indexing_strategies: list[PipelineIndexingStrategy]


class ReindexResponse(BaseModel):
    indexed_documents: int
    indexed_chunks: int
    skipped_documents: int
    metadata_updated_documents: int
    missing_marked_documents: int
    deleted_documents: int
    errors: list[dict[str, Any]]
