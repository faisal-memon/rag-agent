"""Configuration for the API and document-agent runtime."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field

from app.core.config import ConfiguredSettings, DatabaseSettings


class ApiSettings(ConfiguredSettings):
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    normalized_output_dir: Path = Field(default=Path("/data/normalized"), alias="NORMALIZED_OUTPUT_DIR")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_embedding_model: str = Field(default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL")
    openai_chat_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_CHAT_MODEL")
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llamacpp_base_url: str = Field(default="http://localhost:8080/v1", alias="LLAMACPP_BASE_URL")
    llamacpp_api_key: str = Field(default="not-needed", alias="LLAMACPP_API_KEY")
    llamacpp_chat_model: str = Field(default="local-model", alias="LLAMACPP_CHAT_MODEL")
    query_limit: int = Field(default=8, alias="RAG_QUERY_LIMIT")
    agent_max_steps: int = Field(default=6, ge=1, le=12, alias="RAG_AGENT_MAX_STEPS")
    memory_path: Path = Field(default=Path("/memory/MEMORY.md"), alias="RAG_MEMORY_PATH")
    embedding_provider: str = Field(default="openai", alias="EMBEDDING_PROVIDER")
    embedding_llamacpp_base_url: str = Field(default="http://localhost:8081/v1", alias="LLAMACPP_EMBEDDING_BASE_URL")
    embedding_llamacpp_api_key: str = Field(default="not-needed", alias="LLAMACPP_EMBEDDING_API_KEY")
    embedding_llamacpp_model: str = Field(default="local-embedding-model", alias="LLAMACPP_EMBEDDING_MODEL")
    embedding_query_prefix: str = Field(default="", alias="EMBEDDING_QUERY_PREFIX")
    embedding_document_prefix: str = Field(default="", alias="EMBEDDING_DOCUMENT_PREFIX")


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
