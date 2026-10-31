"""ESPHome Native API connection for ReSpeaker voice sessions."""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from collections.abc import Callable

from aioesphomeapi import (
    APIClient,
    ReconnectLogic,
    VoiceAssistantAudioSettings,
    VoiceAssistantFeature,
)

from app.agent.agent import Agent
from app.agent.config import ApiSettings
from app.agent.transcription import TranscriptionUnavailableError, transcribe_audio

logger = logging.getLogger(__name__)

MAX_SESSION_AUDIO_BYTES = 3_000_000
VOICE_SAMPLE_RATE = 16_000
VOICE_SAMPLE_WIDTH = 2
VOICE_HISTORY_MAX_MESSAGES = 12


class ESPHomeVoiceClient:
    """Maintain a Native API connection and receive ReSpeaker voice audio."""

    def __init__(self, settings: ApiSettings, agent: Agent) -> None:
        self._settings = settings
        self._agent = agent
        self._client: APIClient | None = None
        self._reconnect: ReconnectLogic | None = None
        self._unsubscribe_voice: Callable[[], None] | None = None
        self._session_audio = bytearray()
        self._session_id: str | None = None
        self._session_audio_truncated = False
        self._voice_history: list[dict[str, str]] = []

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
        if self._session_id is None:
            logger.debug("Ignoring ESPHome audio frame outside a voice session")
            return
        if self._session_audio_truncated:
            return
        # ESPHome may split PCM samples across Native API messages, and its
        # optional second channel need not have the same chunk boundaries.
        # Use channel 1 (the default voice channel) and join bytes before WAV
        # framing rather than validating each transport frame independently.
        if len(self._session_audio) + len(data) > MAX_SESSION_AUDIO_BYTES:
            self._session_audio_truncated = True
            logger.warning(
                "ESPHome audio session exceeded the %d-byte in-memory limit",
                MAX_SESSION_AUDIO_BYTES,
            )
            return
        self._session_audio.extend(data)

    async def _handle_session_stop(self, aborted: bool) -> None:
        conversation_id = self._session_id
        audio = bytes(self._session_audio)
        truncated = self._session_audio_truncated
        self._reset_session()
        logger.info(
            "ESPHome voice session ended (conversation=%s, aborted=%s, "
            "audio_bytes=%d, truncated=%s)",
            conversation_id,
            aborted,
            len(audio),
            truncated,
        )
        # aioesphomeapi reports VoiceAssistantRequest(start=False) as an
        # aborted stop. On this satellite, the user button sends that message
        # when the user ends a recording, so preserve and transcribe captured
        # audio even when the protocol marks the stop as aborted.
        if truncated or not audio:
            return

        if len(audio) % VOICE_SAMPLE_WIDTH:
            logger.warning(
                "ESPHome voice session ended with an incomplete PCM sample; "
                "discarding its final byte (conversation=%s)",
                conversation_id,
            )
            audio = audio[:-1]
        if not audio:
            return
        wav_audio = _pcm_to_wav(audio, channels=1)
        try:
            transcript = await asyncio.to_thread(
                transcribe_audio,
                base_url=self._settings.whispercpp_base_url,
                audio=wav_audio,
                filename="respeaker.wav",
                content_type="audio/wav",
            )
        except TranscriptionUnavailableError as exc:
            logger.warning(
                "ReSpeaker transcription failed (conversation=%s): %s",
                conversation_id,
                exc,
            )
            return
        except Exception:
            logger.exception(
                "Unexpected ReSpeaker transcription error (conversation=%s)",
                conversation_id,
            )
            return
        logger.info("ReSpeaker transcription (conversation=%s): %s", conversation_id, transcript)

        self._voice_history.append({"role": "user", "content": transcript})
        self._voice_history = self._voice_history[-VOICE_HISTORY_MAX_MESSAGES:]
        try:
            result = await asyncio.to_thread(
                self._agent.answer,
                transcript,
                history=self._voice_history[:-1],
            )
        except Exception:
            logger.exception(
                "ReSpeaker agent request failed (conversation=%s)",
                conversation_id,
            )
            return

        answer = str(result.get("answer") or "").strip()
        if not answer:
            logger.warning("ReSpeaker agent returned an empty answer (conversation=%s)", conversation_id)
            return
        self._voice_history.append({"role": "assistant", "content": answer})
        self._voice_history = self._voice_history[-VOICE_HISTORY_MAX_MESSAGES:]
        logger.info("ReSpeaker agent response (conversation=%s): %s", conversation_id, answer)

    def _reset_session(self) -> None:
        self._session_id = None
        self._session_audio.clear()
        self._session_audio_truncated = False

    def _unsubscribe(self) -> None:
        unsubscribe = self._unsubscribe_voice
        self._unsubscribe_voice = None
        if unsubscribe is not None:
            unsubscribe()


def _pcm_to_wav(pcm: bytes, *, channels: int) -> bytes:
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(VOICE_SAMPLE_WIDTH)
        wav_file.setframerate(VOICE_SAMPLE_RATE)
        wav_file.writeframes(pcm)
    return wav_buffer.getvalue()
