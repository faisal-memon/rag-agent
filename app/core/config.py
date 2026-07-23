"""Shared configuration primitives with no runtime-specific dependencies."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SETTINGS_CONFIG = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class ConfiguredSettings(BaseSettings):
    """Base settings class with the project's shared environment behavior."""

    model_config = SETTINGS_CONFIG


class DatabaseSettings(ConfiguredSettings):
    db: str = Field(default="rag", alias="POSTGRES_DB")
    user: str = Field(default="rag", alias="POSTGRES_USER")
    password: str = Field(default="rag", alias="POSTGRES_PASSWORD")
    host: str = Field(default="localhost", alias="POSTGRES_HOST")
    port: int = Field(default=5432, alias="POSTGRES_PORT")

    @property
    def url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


class CommonSettings(ConfiguredSettings):
    """Settings shared by the API, embedding, and normalization runtimes."""

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    nextcloud_source_dir: Path = Field(default=Path("/data/nextcloud"), alias="NEXTCLOUD_SOURCE_DIR")
    normalized_output_dir: Path = Field(default=Path("/data/normalized"), alias="NORMALIZED_OUTPUT_DIR")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_embedding_model: str = Field(default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL")
    openai_chat_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_CHAT_MODEL")


__all__ = [
    "CommonSettings",
    "ConfiguredSettings",
    "DatabaseSettings",
]
