"""Shared configuration primitives with no runtime-specific dependencies."""

import json
import os
import tempfile
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
    data = read_runtime_settings()
    values = data.get(section, {})
    if not isinstance(values, dict):
        raise ValueError(f"runtime settings section {section!r} must be an object")
    return {key: value for key, value in values.items() if key not in (excluded or set())}


def runtime_settings_path() -> Path:
    """Return the persisted runtime settings file path."""
    return Path(os.environ.get("RAG_SETTINGS_PATH", "config/runtime-settings.json"))


def read_runtime_settings() -> dict[str, Any]:
    """Read the persisted runtime settings, returning empty settings when absent."""
    path = runtime_settings_path()
    if not path.exists():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("runtime settings must be a JSON object")
    return data


def write_runtime_settings(data: dict[str, Any]) -> None:
    """Atomically replace the persisted runtime settings file."""
    path = runtime_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        json.dump(data, temporary, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


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
    "read_runtime_settings",
    "runtime_settings",
    "runtime_settings_path",
    "write_runtime_settings",
]
