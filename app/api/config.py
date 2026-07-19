"""Configuration for the API and document-agent runtime."""

from pathlib import Path

from pydantic import Field

from app.core.settings import ConfiguredSettings


class ApiSettings(ConfiguredSettings):
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llamacpp_base_url: str = Field(default="http://localhost:8080/v1", alias="LLAMACPP_BASE_URL")
    llamacpp_api_key: str = Field(default="not-needed", alias="LLAMACPP_API_KEY")
    llamacpp_chat_model: str = Field(default="local-model", alias="LLAMACPP_CHAT_MODEL")
    query_limit: int = Field(default=8, alias="RAG_QUERY_LIMIT")
    agent_max_steps: int = Field(default=6, ge=1, le=12, alias="RAG_AGENT_MAX_STEPS")
    memory_path: Path = Field(default=Path("/memory/MEMORY.md"), alias="RAG_MEMORY_PATH")
