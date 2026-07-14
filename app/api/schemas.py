from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]


class AgentChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)


class AgentQueryRequest(BaseModel):
    question: str = Field(min_length=1)
    history: list[AgentChatMessage] = Field(default_factory=list, max_length=20)


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
    answer: str
    plan: list[AgentToolCall]
    tool_results: list[AgentToolResult]
    reasoning: list[AgentReasoningStep]
    citations: list[Citation]


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
