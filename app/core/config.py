from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


SETTINGS_CONFIG = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class DatabaseSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    db: str = Field(default="rag", alias="POSTGRES_DB")
    user: str = Field(default="rag", alias="POSTGRES_USER")
    password: str = Field(default="rag", alias="POSTGRES_PASSWORD")
    host: str = Field(default="localhost", alias="POSTGRES_HOST")
    port: int = Field(default=5432, alias="POSTGRES_PORT")

    @property
    def url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


class CommonSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    nextcloud_source_dir: Path = Field(default=Path("/data/nextcloud"), alias="NEXTCLOUD_SOURCE_DIR")
    normalized_output_dir: Path = Field(default=Path("/data/normalized"), alias="NORMALIZED_OUTPUT_DIR")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_embedding_model: str = Field(default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL")
    openai_chat_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_CHAT_MODEL")


class ApiSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llamacpp_base_url: str = Field(default="http://localhost:8080/v1", alias="LLAMACPP_BASE_URL")
    llamacpp_api_key: str = Field(default="not-needed", alias="LLAMACPP_API_KEY")
    llamacpp_chat_model: str = Field(default="local-model", alias="LLAMACPP_CHAT_MODEL")
    query_limit: int = Field(default=8, alias="RAG_QUERY_LIMIT")
    agent_max_steps: int = Field(default=6, ge=1, le=12, alias="RAG_AGENT_MAX_STEPS")
    memory_path: Path = Field(default=Path("/memory/MEMORY.md"), alias="RAG_MEMORY_PATH")


class EmbedSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    provider: str = Field(default="openai", alias="EMBEDDING_PROVIDER")
    llamacpp_base_url: str = Field(default="http://localhost:8081/v1", alias="LLAMACPP_EMBEDDING_BASE_URL")
    llamacpp_api_key: str = Field(default="not-needed", alias="LLAMACPP_EMBEDDING_API_KEY")
    llamacpp_model: str = Field(default="local-embedding-model", alias="LLAMACPP_EMBEDDING_MODEL")
    tokenizer_model_id: str = Field(
        default="mixedbread-ai/mxbai-embed-large-v1",
        alias="EMBEDDING_TOKENIZER_MODEL_ID",
    )
    tokenizer_local_files_only: bool = Field(default=False, alias="EMBEDDING_TOKENIZER_LOCAL_FILES_ONLY")
    query_prefix: str = Field(default="", alias="EMBEDDING_QUERY_PREFIX")
    document_prefix: str = Field(default="", alias="EMBEDDING_DOCUMENT_PREFIX")
    chunk_size: int = Field(default=500, alias="RAG_CHUNK_SIZE")
    chunk_overlap: int = Field(default=75, alias="RAG_CHUNK_OVERLAP")
    missing_grace_days: int = Field(default=7, alias="RAG_MISSING_GRACE_DAYS")
    indexing_version: str = Field(default="tokenizer-aligned-v1", alias="RAG_INDEXING_VERSION")
    watch_debounce_seconds: float = Field(default=15.0, ge=0.0, alias="EMBED_WATCH_DEBOUNCE_SECONDS")
    reconcile_interval_seconds: float = Field(default=15.0, ge=0.0, alias="EMBED_RECONCILE_INTERVAL_SECONDS")


class NormalizeSettings(BaseSettings):
    model_config = SETTINGS_CONFIG

    backend: str = Field(default="docling", alias="NORMALIZATION_BACKEND")
    enabled_suffixes_csv: str = Field(default=".pdf,.docx,.txt,.md,.jpg,.jpeg,.png", alias="RAG_ENABLED_SUFFIXES")
    watch_debounce_seconds: float = Field(default=15.0, ge=0.0, alias="NORMALIZE_WATCH_DEBOUNCE_SECONDS")
    reconcile_interval_seconds: float = Field(default=15.0, ge=0.0, alias="NORMALIZE_RECONCILE_INTERVAL_SECONDS")
    stability_checks: int = Field(default=3, ge=1, alias="NORMALIZE_WATCH_STABILITY_CHECKS")
    stability_interval_seconds: float = Field(default=2.0, ge=0.0, alias="NORMALIZE_WATCH_STABILITY_INTERVAL_SECONDS")
    stability_max_wait_seconds: float = Field(default=30.0, ge=0.0, alias="NORMALIZE_WATCH_STABILITY_MAX_WAIT_SECONDS")

    @property
    def enabled_suffixes(self) -> set[str]:
        return {
            suffix if suffix.startswith(".") else f".{suffix}"
            for suffix in (item.strip().lower() for item in self.enabled_suffixes_csv.split(","))
            if suffix
        }


class Settings(BaseSettings):
    model_config = SETTINGS_CONFIG

    common: CommonSettings = Field(default_factory=CommonSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    embed: EmbedSettings = Field(default_factory=EmbedSettings)
    normalize: NormalizeSettings = Field(default_factory=NormalizeSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
