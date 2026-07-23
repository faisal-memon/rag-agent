"""Shared configuration primitives with no runtime-specific dependencies."""

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


__all__ = [
    "ConfiguredSettings",
    "DatabaseSettings",
]
