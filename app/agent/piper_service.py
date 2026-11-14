"""Shared access to the configured Piper text-to-speech service."""

from __future__ import annotations

from app.agent.config import ApiSettings
from app.agent.esphome_playback import PiperClient


class PiperService:
    """Keep Piper configuration in one shared application service."""

    def __init__(self, settings: ApiSettings) -> None:
        self.update_settings(settings)

    def update_settings(self, settings: ApiSettings) -> None:
        """Apply the Piper connection and voice used by future requests."""
        self._client = PiperClient(
            settings.piper_host,
            settings.piper_port,
            settings.piper_voice,
        )

    async def list_voices(self) -> list[str]:
        """Return Piper voices installed on the configured service."""
        return await self._client.list_voices()

    async def synthesize_flac(self, text: str) -> bytes:
        """Synthesize text with the configured voice."""
        return await self._client.synthesize_flac(text)
