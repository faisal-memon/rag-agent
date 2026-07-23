"""Configuration for normalized-document embedding and reconciliation."""

from pydantic import Field

from app.core.config import ConfiguredSettings


class EmbedSettings(ConfiguredSettings):
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
