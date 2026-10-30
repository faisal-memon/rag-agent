"""Configuration for the API and document-agent runtime."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field

from app.core.config import ConfiguredSettings, DatabaseSettings, runtime_settings


class ApiSettings(ConfiguredSettings):
    timezone: ZoneInfo = Field(default=ZoneInfo("UTC"), alias="TZ")
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    normalized_output_dir: Path = Field(default=Path("/data/normalized"), alias="NORMALIZED_OUTPUT_DIR")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_embedding_model: str = Field(default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL")
    openai_chat_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_CHAT_MODEL")
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llamacpp_base_url: str = Field(default="http://localhost:8080/v1", alias="LLAMACPP_BASE_URL")
    llamacpp_api_key: str = Field(default="not-needed", alias="LLAMACPP_API_KEY")
    llamacpp_chat_model: str = Field(default="local-model", alias="LLAMACPP_CHAT_MODEL")
    whispercpp_base_url: str = Field(default="http://whispercpp:8080", alias="WHISPERCPP_BASE_URL")
    esphome_device_host: str = Field(default="", alias="ESPHOME_DEVICE_HOST")
    esphome_device_port: int = Field(default=6053, ge=1, le=65535, alias="ESPHOME_DEVICE_PORT")
    esphome_device_name: str = Field(default="", alias="ESPHOME_DEVICE_NAME")
    esphome_api_key: str = Field(default="", alias="ESPHOME_API_KEY")
    query_limit: int = Field(default=8, alias="RAG_QUERY_LIMIT")
    agent_max_steps: int = Field(default=6, ge=1, le=12, alias="RAG_AGENT_MAX_STEPS")
    home_latitude: float | None = Field(default=None, ge=-90, le=90, alias="HOME_LATITUDE")
    home_longitude: float | None = Field(default=None, ge=-180, le=180, alias="HOME_LONGITUDE")
    memory_path: Path = Field(default=Path("/memory/MEMORY.md"), alias="RAG_MEMORY_PATH")
    memory_enabled: bool = Field(default=True, alias="RAG_MEMORY_ENABLED")
    memory_require_approval: bool = Field(default=True, alias="RAG_MEMORY_REQUIRE_APPROVAL")
    embedding_provider: str = Field(default="openai", alias="EMBEDDING_PROVIDER")
    embedding_llamacpp_base_url: str = Field(default="http://localhost:8081/v1", alias="LLAMACPP_EMBEDDING_BASE_URL")
    embedding_llamacpp_api_key: str = Field(default="not-needed", alias="LLAMACPP_EMBEDDING_API_KEY")
    embedding_llamacpp_model: str = Field(default="local-embedding-model", alias="LLAMACPP_EMBEDDING_MODEL")
    embedding_query_prefix: str = Field(default="", alias="EMBEDDING_QUERY_PREFIX")
    embedding_document_prefix: str = Field(default="", alias="EMBEDDING_DOCUMENT_PREFIX")
    mcp_registry_url: str = Field(default="http://mcp-registry/registry.json", alias="MCP_REGISTRY_URL")
    mcp_registry_refresh_interval: float = Field(
        default=300.0, gt=0, alias="MCP_REGISTRY_REFRESH_INTERVAL"
    )


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings(
        **runtime_settings(
            "api",
            excluded={
                "database",
                "openai_api_key",
                "llamacpp_api_key",
                "embedding_llamacpp_api_key",
                "esphome_api_key",
            },
        )
    )
