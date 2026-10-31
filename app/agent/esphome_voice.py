"""ESPHome Native API connection for ReSpeaker voice sessions."""

from __future__ import annotations

import logging
from collections.abc import Callable

from aioesphomeapi import (
    APIClient,
    ReconnectLogic,
    VoiceAssistantAudioSettings,
    VoiceAssistantFeature,
)

from app.agent.config import ApiSettings

logger = logging.getLogger(__name__)

MAX_SESSION_AUDIO_BYTES = 3_000_000


class ESPHomeVoiceClient:
    """Maintain a Native API connection and receive ReSpeaker voice audio."""

    def __init__(self, settings: ApiSettings) -> None:
        self._settings = settings
        self._client: APIClient | None = None
        self._reconnect: ReconnectLogic | None = None
        self._unsubscribe_voice: Callable[[], None] | None = None
        self._session_audio = bytearray()
        self._session_id: str | None = None
        self._session_audio_truncated = False

    async def start(self) -> None:
        """Start reconnecting to the configured device; leave it disabled by default."""
        if not self._settings.esphome_device_host:
            logger.info("ESPHome voice client disabled; ESPHOME_DEVICE_HOST is unset")
            return

        self._client = APIClient(
            self._settings.esphome_device_host,
            self._settings.esphome_device_port,
            client_info="RAG Agent",
            noise_psk=self._settings.esphome_api_key or None,
            expected_name=self._settings.esphome_device_name or None,
        )
        self._reconnect = ReconnectLogic(
            client=self._client,
            name=self._settings.esphome_device_host,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_connect_error=self._on_connect_error,
            zeroconf_wake=False,
        )
        await self._reconnect.start()
        logger.info(
            "ESPHome voice client started for %s:%s",
            self._settings.esphome_device_host,
            self._settings.esphome_device_port,
        )

    async def stop(self) -> None:
        """Unsubscribe and close the Native API connection on application shutdown."""
        if self._reconnect is not None:
            await self._reconnect.stop()
        self._unsubscribe()
        if self._client is not None:
            await self._client.disconnect()

    async def _on_connect(self) -> None:
        assert self._client is not None
        info = await self._client.device_info()
        feature_flags = info.voice_assistant_feature_flags_compat(
            self._client.api_version
        )
        if not feature_flags & VoiceAssistantFeature.VOICE_ASSISTANT:
            logger.error("ESPHome device %s has no Voice Assistant capability", info.name)
            return
        if not feature_flags & VoiceAssistantFeature.API_AUDIO:
            logger.error("ESPHome device %s does not support Native API audio", info.name)
            return

        self._unsubscribe_voice = self._client.subscribe_voice_assistant(
            handle_start=self._handle_session_start,
            handle_stop=self._handle_session_stop,
            handle_audio=self._handle_audio,
        )
        logger.info(
            "Connected to ESPHome device %s (%s); Native API audio subscribed",
            info.name,
            info.mac_address,
        )

    async def _on_disconnect(self, expected: bool) -> None:
        self._unsubscribe()
        logger.info("ESPHome Native API disconnected (expected=%s)", expected)

    async def _on_connect_error(self, error: Exception) -> None:
        logger.warning("Could not connect to ESPHome device: %s", error)

    async def _handle_session_start(
        self,
        conversation_id: str,
        flags: int,
        audio_settings: VoiceAssistantAudioSettings,
        wake_word_phrase: str | None,
    ) -> int:
        self._session_id = conversation_id
        self._session_audio.clear()
        self._session_audio_truncated = False
        logger.info(
            "ESPHome voice session started (conversation=%s, flags=%s, "
            "wake_word=%s, audio_settings=%s)",
            conversation_id,
            flags,
            wake_word_phrase,
            audio_settings,
        )
        # API_AUDIO sends audio frames on this already-open Native API connection.
        return 0

    async def _handle_audio(self, data: bytes, data2: bytes | None = None) -> None:
        if self._session_audio_truncated:
            return
        if len(self._session_audio) + len(data) > MAX_SESSION_AUDIO_BYTES:
            self._session_audio_truncated = True
            logger.warning("ESPHome audio session exceeded the in-memory size limit")
            return
        self._session_audio.extend(data)

    async def _handle_session_stop(self, aborted: bool) -> None:
        logger.info(
            "ESPHome voice session ended (conversation=%s, aborted=%s, "
            "audio_bytes=%d, truncated=%s)",
            self._session_id,
            aborted,
            len(self._session_audio),
            self._session_audio_truncated,
        )
        self._session_id = None
        self._session_audio.clear()
        self._session_audio_truncated = False

    def _unsubscribe(self) -> None:
        unsubscribe = self._unsubscribe_voice
        self._unsubscribe_voice = None
        if unsubscribe is not None:
            unsubscribe()
