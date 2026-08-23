"""Shared configuration primitives with no runtime-specific dependencies."""

import json
import os
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    populate_by_name=True,
)


class ConfiguredSettings(BaseSettings):
    """Base settings class with the project's shared environment behavior."""

    model_config = SETTINGS_CONFIG


def runtime_settings(section: str, *, excluded: set[str] | None = None) -> dict[str, Any]:
    """Read one component's non-secret settings from the shared JSON file."""
    path = Path(os.environ.get("RAG_SETTINGS_PATH", "config/runtime-settings.json"))
    if not path.exists():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("runtime settings must be a JSON object")
    values = data.get(section, {})
    if not isinstance(values, dict):
        raise ValueError(f"runtime settings section {section!r} must be an object")
    return {key: value for key, value in values.items() if key not in (excluded or set())}


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
    "runtime_settings",
]
