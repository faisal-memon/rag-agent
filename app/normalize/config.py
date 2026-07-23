"""Configuration for raw-document normalization and reconciliation."""

from pydantic import Field

from app.core.config import ConfiguredSettings


class NormalizeSettings(ConfiguredSettings):
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
