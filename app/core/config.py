"""Assemble the shared and runtime-specific configuration sections."""

from functools import lru_cache

from pydantic import BaseModel, Field

from app.api.config import ApiSettings
from app.core.settings import CommonSettings, DatabaseSettings
from app.embed.config import EmbedSettings
from app.normalize.config import NormalizeSettings


class Settings(BaseModel):
    """One cached view of configuration, grouped by runtime ownership."""

    common: CommonSettings = Field(default_factory=CommonSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    embed: EmbedSettings = Field(default_factory=EmbedSettings)
    normalize: NormalizeSettings = Field(default_factory=NormalizeSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()


__all__ = [
    "ApiSettings",
    "CommonSettings",
    "DatabaseSettings",
    "EmbedSettings",
    "NormalizeSettings",
    "Settings",
    "get_settings",
]
