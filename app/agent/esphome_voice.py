"""ESPHome Native API connection for ReSpeaker voice sessions."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import wave
from collections.abc import Callable
from datetime import datetime, timezone

from aioesphomeapi import (
    APIClient,
    MediaPlayerInfo,
    ReconnectLogic,
    VoiceAssistantEventType,
    VoiceAssistantAudioSettings,
    VoiceAssistantFeature,
)

from app.agent import voice_devices
from app.agent.agent import Agent
from app.agent.config import ApiSettings
from app.agent.esphome_playback import OneShotAudioServer, PiperClient
from app.agent.transcription import TranscriptionUnavailableError, transcribe_audio
from app.agent.voice_activity import (
    VOICE_SAMPLE_RATE,
    CaptureEvent,
    VoiceActivityDetector,
)

logger = logging.getLogger(__name__)

MAX_SESSION_AUDIO_BYTES = 3_000_000
VOICE_SAMPLE_WIDTH = 2
VOICE_HISTORY_MAX_MESSAGES = 12
UNINTELLIGIBLE_TRANSCRIPTS = {
    "blank audio",
    "inaudible",
    "inaudible speech",
    "indistinct",
    "unintelligible",
    "unintelligible speech",
    "no speech detected",
    "applause",
    "crowd cheering",
    "cheering",
    "music",
    "upbeat music",
    "background music",
    "laughter",
    "laughing",
    "silence",
    "noise",
}
REPEAT_REQUEST = "Sorry, I didn't catch that. Could you please repeat it?"


class ESPHomeVoiceClient:
    """Maintain a Native API connection and receive ReSpeaker voice audio."""

    def __init__(self, settings: ApiSettings, agent: Agent | None = None) -> None:
        self._settings = settings
        self._agent = agent
        self._piper = PiperClient(
            settings.piper_host,
            settings.piper_port,
            settings.piper_voice,
        )
        self._media_server = (
            OneShotAudioServer(
                settings.esphome_media_server_host,
                settings.esphome_media_server_port,
                settings.esphome_media_url_base,
            )
            if settings.esphome_media_url_base
            else None
        )
        if self._media_server is not None:
            self._media_server.set_fetch_callback(self._on_audio_fetched)
        self._client: APIClient | None = None
        self._media_player_key: int | None = None
        self._reconnect: ReconnectLogic | None = None
        self._unsubscribe_voice: Callable[[], None] | None = None
        self._session_audio = bytearray()
        self._session_id: str | None = None
        self._session_audio_truncated = False
        self._vad: VoiceActivityDetector | None = None
        self._vad_ended = False
        self._vad_no_speech_timeout = False
        self._last_vad_diagnostic_at = 0.0
        self._follow_up_pending = False
        self._session_is_follow_up = False
        self._session_started_at = 0.0
        self._turn_started_at = 0.0
        self._activity_write: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._device_id: str | None = None
        self._progress_audio_fetched: asyncio.Event | None = None
        self._device_name = settings.esphome_device_name or "ESPHome speaker"
        self._connected = False

    async def start(self) -> None:
        """Start reconnecting to the configured device; leave it disabled by default."""
        self._loop = asyncio.get_running_loop()
        if not self._settings.esphome_device_host:
            logger.info("ESPHome voice client disabled; ESPHOME_DEVICE_HOST is unset")
            return

        if self._media_server is not None:
            await self._media_server.start()

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
        if self._media_server is not None:
            await self._media_server.stop()

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

        entities, _ = await self._client.list_entities_services()
        media_player = next(
            (
                entity
                for entity in entities
                if isinstance(entity, MediaPlayerInfo)
                and entity.object_id == "media_player"
            ),
            None,
        )
        self._media_player_key = media_player.key if media_player is not None else None
        if self._media_server is not None and self._media_player_key is None:
            logger.error("ESPHome device %s has no Media Player entity", info.name)

        self._unsubscribe_voice = self._client.subscribe_voice_assistant(
            handle_start=self._handle_session_start,
            handle_stop=self._handle_session_stop,
            handle_audio=self._handle_audio,
        )
        self._finish_voice_run(error="api-reconnected")
        device_id = _stable_device_id(info.mac_address, self._settings.esphome_device_host)
        self._device_id = device_id
        self._device_name = info.name or self._device_name
        self._connected = True
        try:
            await asyncio.to_thread(
                voice_devices.register_device,
                device_id,
                self._device_name,
                self._settings.esphome_device_host,
                self._settings.esphome_device_port,
            )
        except Exception:
            logger.exception("Could not persist ESPHome device registration")
            self._device_id = None
        logger.info(
            "Connected to ESPHome device %s (%s); Native API audio subscribed",
            info.name,
            info.mac_address,
        )

    async def _on_disconnect(self, expected: bool) -> None:
        self._connected = False
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
        self._session_is_follow_up = (
            self._follow_up_pending and not wake_word_phrase
        )
        self._follow_up_pending = False
        startup_grace_ms = (
            0 if self._session_is_follow_up else self._settings.voice_start_grace_ms
        )
        self._session_id = conversation_id
        # Keep audio from the beginning so follow-up start-gate confirmation
        # can include a small pre-roll and never clip the first syllable.
        self._session_audio.clear()
        self._session_audio_truncated = False
        self._vad = VoiceActivityDetector(
            startup_grace_ms=startup_grace_ms,
            end_silence_ms=self._settings.voice_end_silence_ms,
            window_frames=self._settings.voice_vad_window_frames,
            window_speech_frames=self._settings.voice_vad_window_speech_frames,
            activity_history=self._settings.voice_vad_activity_history,
            active_windows_required=self._settings.voice_vad_active_windows_required,
            start_gate=self._session_is_follow_up,
            capture_limit_seconds=self._settings.voice_capture_limit_seconds,
            no_speech_timeout_seconds=(
                self._settings.voice_follow_up_start_timeout_seconds
                if self._session_is_follow_up
                else 15
            ),
        )
        self._vad_ended = False
        self._vad_no_speech_timeout = False
        self._session_started_at = time.monotonic()
        self._last_vad_diagnostic_at = self._session_started_at
        self._turn_started_at = self._session_started_at
        logger.info(
            "ESPHome voice session started (conversation=%s, flags=%s, "
            "wake_word=%s, follow_up=%s, audio_settings=%s)",
            conversation_id,
            flags,
            wake_word_phrase,
            self._session_is_follow_up,
            audio_settings,
        )
        self._record_activity("Voice session", "started")
        if self._client is not None:
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START, None
            )
            if wake_word_phrase:
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_END,
                    {"wake_word": wake_word_phrase},
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
        await self._update_vad(data)

    async def _update_vad(self, data: bytes) -> None:
        """Signal ESPHome when speech begins and ends so its mic stream closes."""
        if self._client is None or self._vad_ended or self._vad is None:
            return
        events = self._vad.process(data)
        now = time.monotonic()
        if events or now - self._last_vad_diagnostic_at >= 1.0:
            diagnostic = self._vad.take_diagnostics()
            if diagnostic["frames"]:
                logger.info(
                    "ReSpeaker VAD diagnostics (state=%s, frames=%d, speech=%d, "
                    "non_speech=%d, grace=%d, silence_ms=%d, silence_resets=%d, "
                    "longest_non_speech_ms=%d, endpoint_quiet_ms=%d, "
                    "rms_avg=%s, rms_min=%s, rms_max=%s, peak_max=%s, "
                    "start_gate=%s, start_gate_windows=%d, "
                    "start_gate_active_windows=%d, start_gate_confirmed=%s)",
                    diagnostic["state"],
                    diagnostic["frames"],
                    diagnostic["speech_frames"],
                    diagnostic["non_speech_frames"],
                    diagnostic["grace_frames"],
                    diagnostic["silence_ms"],
                    diagnostic["silence_resets"],
                    diagnostic["longest_non_speech_ms"],
                    diagnostic["endpoint_quiet_ms"],
                    diagnostic["rms_avg"],
                    diagnostic["rms_min"],
                    diagnostic["rms_max"],
                    diagnostic["peak_max"],
                    diagnostic["start_gate"],
                    diagnostic["start_gate_windows"],
                    diagnostic["start_gate_active_windows"],
                    diagnostic["start_gate_confirmed"],
                )
            self._last_vad_diagnostic_at = now
        for event in events:
            if event is CaptureEvent.SPEECH_STARTED:
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_STT_START, None
                )
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START, None
                )
                logger.info("ReSpeaker VAD state: waiting_for_speech -> speaking")
            elif event is CaptureEvent.SPEECH_ENDED:
                self._vad_ended = True
                logger.info("ReSpeaker VAD state: waiting_for_silence -> ended")
                self._record_activity(
                    "Capture", "completed", duration_ms=self._elapsed_ms(), detail="Ended after silence"
                )
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END, None
                )
                asyncio.create_task(self._handle_session_stop(False))
                return
            elif event is CaptureEvent.MAX_DURATION_REACHED:
                self._vad_ended = True
                logger.warning("ReSpeaker capture reached its configured time limit")
                self._record_activity(
                    "Capture", "completed", duration_ms=self._elapsed_ms(), detail="Capture limit reached"
                )
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END, None
                )
                asyncio.create_task(self._handle_session_stop(False))
                return
            elif event is CaptureEvent.NO_SPEECH_TIMEOUT:
                self._vad_ended = True
                self._vad_no_speech_timeout = True
                logger.info(
                    "ReSpeaker capture ended: no speech detected (follow_up=%s, start_gate=%s)",
                    self._session_is_follow_up,
                    self._session_is_follow_up,
                )
                self._record_activity(
                    "Capture", "info", duration_ms=self._elapsed_ms(), detail="No speech detected"
                )
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END, None
                )
                asyncio.create_task(self._handle_session_stop(False))
                return

    def update_settings(self, settings: ApiSettings) -> None:
        """Apply settings updates to subsequent voice sessions."""
        self._settings = settings

    def status(self) -> dict:
        """Return the currently configured speaker's live connection details."""
        return {
            "device_id": self._device_id,
            "display_name": self._device_name,
            "host": self._settings.esphome_device_host,
            "port": self._settings.esphome_device_port,
            "connected": self._connected,
        }

    async def _handle_session_stop(self, aborted: bool) -> None:
        speaker_created_at = datetime.now(timezone.utc)
        capture_seconds = (
            time.monotonic() - self._session_started_at
            if self._session_started_at
            else 0.0
        )
        conversation_id = self._session_id
        audio = bytes(self._session_audio)
        truncated = self._session_audio_truncated
        vad_ended = self._vad_ended
        no_speech_timeout = self._vad_no_speech_timeout
        session_is_follow_up = self._session_is_follow_up
        self._reset_session()
        logger.info(
            "ESPHome voice session ended (conversation=%s, aborted=%s, "
            "audio_bytes=%d, truncated=%s, capture_seconds=%.2f)",
            conversation_id,
            aborted,
            len(audio),
            truncated,
            capture_seconds,
        )
        if aborted:
            self._record_activity(
                "Capture",
                "info",
                duration_ms=round(capture_seconds * 1000),
                detail="Stopped by speaker",
            )
        # aioesphomeapi reports VoiceAssistantRequest(start=False) as an
        # aborted stop. On this satellite, the user button sends that message
        # when the user ends a recording, so preserve and transcribe captured
        # audio even when the protocol marks the stop as aborted.
        if no_speech_timeout:
            if self._client is not None:
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
                    {"text": ""},
                )
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END,
                    {"conversation_id": "", "continue_conversation": "0"},
                )
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, None
                )
            if session_is_follow_up:
                self._record_activity(
                    "Follow-up listening",
                    "completed",
                    duration_ms=round(capture_seconds * 1000),
                    detail="No request; returned to wake-word listening",
                )
            return
        if truncated or not audio:
            self._record_activity(
                "Capture",
                "failed" if truncated else "info",
                duration_ms=round(capture_seconds * 1000),
                detail="Audio buffer limit reached" if truncated else "No audio received",
            )
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
        stage_started = time.monotonic()
        try:
            self._record_activity("Transcription", "started")
            transcript = await asyncio.to_thread(
                transcribe_audio,
                base_url=self._settings.whispercpp_base_url,
                audio=wav_audio,
                filename="respeaker.wav",
                content_type="audio/wav",
            )
        except TranscriptionUnavailableError as exc:
            self._record_activity("Transcription", "failed", duration_ms=self._stage_ms(stage_started))
            logger.warning(
                "ReSpeaker transcription failed (conversation=%s): %s",
                conversation_id,
                exc,
            )
            return
        except Exception:
            self._record_activity("Transcription", "failed", duration_ms=self._stage_ms(stage_started))
            logger.exception(
                "Unexpected ReSpeaker transcription error (conversation=%s)",
                conversation_id,
            )
            return
        logger.info(
            "ReSpeaker transcription complete (conversation=%s, seconds=%.2f, characters=%d)",
            conversation_id,
            time.monotonic() - stage_started,
            len(transcript),
        )
        if _is_unintelligible_transcript(transcript):
            self._record_activity(
                "Transcription",
                "failed",
                duration_ms=self._stage_ms(stage_started),
                detail="No intelligible speech detected",
            )
            logger.info(
                "ReSpeaker transcript was an unintelligible placeholder; skipping agent request "
                "(conversation=%s)",
                conversation_id,
            )
            if vad_ended and self._client is not None:
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
                    {"text": ""},
                )
            await self._speak_reply(REPEAT_REQUEST, conversation_id)
            return
        self._record_activity("Transcription", "completed", duration_ms=self._stage_ms(stage_started))

        if vad_ended and self._client is not None:
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
                {"text": transcript},
            )

        if self._agent is None:
            logger.debug("ReSpeaker agent is not configured; skipping model request")
            return

        device_id = self._device_id
        if not device_id:
            logger.warning("ReSpeaker session has no registered device identity; skipping agent request")
            return
        try:
            history = await asyncio.to_thread(
                voice_devices.load_history,
                device_id,
                VOICE_HISTORY_MAX_MESSAGES,
            )
        except Exception:
            logger.exception("Could not load ReSpeaker conversation history")
            history = []
        history.append({"role": "user", "content": transcript})
        stage_started = time.monotonic()
        progress_queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_progress(event: dict) -> None:
            if event.get("type") != "tool_call":
                return
            loop.call_soon_threadsafe(
                progress_queue.put_nowait,
                _spoken_tool_status(event.get("status")),
            )

        async def announce_progress() -> None:
            while (message := await progress_queue.get()) is not None:
                await self._speak_progress(message, conversation_id)

        progress_task = asyncio.create_task(announce_progress())
        result = None
        try:
            self._record_activity("Assistant", "started")
            result = await asyncio.to_thread(
                self._agent.answer,
                transcript,
                history=history,
                on_progress=on_progress,
                response_mode="spoken",
            )
        except Exception:
            self._record_activity("Assistant", "failed", duration_ms=self._stage_ms(stage_started))
            logger.exception(
                "ReSpeaker agent request failed (conversation=%s)",
                conversation_id,
            )
        finally:
            if result is None:
                progress_task.cancel()
            else:
                progress_queue.put_nowait(None)
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        if result is None:
            self._finish_voice_run(error="assistant-failed")
            return

        answer = str(result.get("answer") or "").strip()
        if not answer:
            self._record_activity("Assistant", "failed", detail="Empty response")
            logger.warning("ReSpeaker agent returned an empty answer (conversation=%s)", conversation_id)
            self._finish_voice_run(error="empty-answer")
            return
        assistant_created_at = datetime.now(timezone.utc)
        try:
            await asyncio.to_thread(
                voice_devices.save_turn,
                device_id,
                transcript,
                answer,
                VOICE_HISTORY_MAX_MESSAGES,
                speaker_created_at=speaker_created_at,
                assistant_created_at=assistant_created_at,
            )
        except Exception:
            logger.exception("Could not save ReSpeaker conversation turn")
        logger.info(
            "ReSpeaker agent response complete (conversation=%s, seconds=%.2f, characters=%d)",
            conversation_id,
            time.monotonic() - stage_started,
            len(answer),
        )
        self._record_activity("Assistant", "completed", duration_ms=self._stage_ms(stage_started))

        continue_listening = (
            self._settings.voice_follow_up_timeout_seconds > 0
            and result.get("continue_listening") is True
        )
        logger.info(
            "ReSpeaker follow-up decision (continue_listening=%s, timeout_seconds=%d)",
            continue_listening,
            self._settings.voice_follow_up_timeout_seconds,
        )
        await self._speak_reply(
            answer,
            conversation_id,
            continue_conversation=continue_listening,
        )

    async def _speak_progress(self, message: str, conversation_id: str) -> None:
        """Speak a tool-call update without ending the active voice turn."""
        if self._media_server is None or self._media_player_key is None or self._client is None:
            return
        try:
            flac_audio = await asyncio.wait_for(self._piper.synthesize_flac(message), timeout=15)
            self._progress_audio_fetched = asyncio.Event()
            media_url = self._media_server.publish(flac_audio)
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START,
                {"text": message},
            )
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END,
                {"url": media_url},
            )
            logger.info(
                "ReSpeaker progress update queued (conversation=%s, message=%r)",
                conversation_id,
                message,
            )
            try:
                await asyncio.wait_for(self._progress_audio_fetched.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.debug("Speaker did not fetch the progress update before timeout")
            await asyncio.sleep(min(4.0, max(1.0, len(message.split()) * 0.42)))
        except Exception:
            logger.exception("Could not speak ReSpeaker progress update (conversation=%s)", conversation_id)
        finally:
            self._progress_audio_fetched = None

    async def _speak_reply(
        self,
        answer: str,
        conversation_id: str,
        *,
        continue_conversation: bool | None = None,
    ) -> None:
        if self._media_server is None:
            self._record_activity(
                "Speaker audio", "failed", detail="Audio listener is not configured"
            )
            logger.debug("ReSpeaker playback is disabled; ESPHOME_MEDIA_URL_BASE is unset")
            self._finish_voice_run()
            return
        if self._media_player_key is None or self._client is None:
            self._record_activity(
                "Speaker audio", "failed", detail="Speaker media player is unavailable"
            )
            logger.warning("ReSpeaker reply could not be played; Media Player is unavailable")
            self._finish_voice_run()
            return

        try:
            stage_started = time.monotonic()
            self._record_activity("Speech generation", "started")
            flac_audio = await self._piper.synthesize_flac(answer)
            logger.info(
                "ReSpeaker synthesis complete (conversation=%s, seconds=%.2f, audio_bytes=%d)",
                conversation_id,
                time.monotonic() - stage_started,
                len(flac_audio),
            )
            self._record_activity("Speech generation", "completed", duration_ms=self._stage_ms(stage_started))
            media_url = self._media_server.publish(flac_audio)
            should_continue = (
                self._settings.voice_follow_up_timeout_seconds > 0
                if continue_conversation is None
                else continue_conversation
            )
            self._follow_up_pending = should_continue
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_START, None
            )
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END,
                {
                    "conversation_id": "",
                    "continue_conversation": "1" if should_continue else "0",
                },
            )
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START,
                {"text": answer},
            )
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END,
                {"url": media_url},
            )
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, None
            )
            logger.info("ReSpeaker agent reply queued for speaker playback (conversation=%s)", conversation_id)
        except Exception:
            logger.exception("ReSpeaker reply synthesis or playback request failed")
            self._record_activity("Speech generation", "failed", duration_ms=self._stage_ms(stage_started))
            self._finish_voice_run()

    def _finish_voice_run(self, *, error: str | None = None) -> None:
        """End the speaker's active turn after playback cannot be completed."""
        self._follow_up_pending = False
        if self._client is None:
            return
        try:
            if error:
                self._client.send_voice_assistant_event(
                    VoiceAssistantEventType.VOICE_ASSISTANT_ERROR,
                    {"code": error, "message": "Voice request ended. Please try again."},
                )
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END,
                {"conversation_id": "", "continue_conversation": "0"},
            )
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END,
                None,
            )
        except Exception:
            logger.exception("Could not send the ReSpeaker voice-session end event")

    def _elapsed_ms(self) -> int:
        started_at = self._session_started_at or self._turn_started_at
        if not started_at:
            return 0
        return max(0, round((time.monotonic() - started_at) * 1000))

    @staticmethod
    def _stage_ms(started_at: float) -> int:
        return max(0, round((time.monotonic() - started_at) * 1000))

    def _record_activity(
        self, stage: str, status: str, *, duration_ms: int | None = None, detail: str | None = None
    ) -> None:
        device_id = self._device_id
        if not device_id:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.debug("Skipping device activity write because its event loop is unavailable")
            return

        def schedule_write() -> None:
            previous = self._activity_write

            async def persist() -> None:
                try:
                    if previous is not None:
                        await previous
                    await asyncio.to_thread(
                        voice_devices.record_activity,
                        device_id,
                        stage,
                        status,
                        duration_ms=duration_ms,
                        detail=detail,
                    )
                except Exception:
                    logger.debug("Could not persist device activity event", exc_info=True)

            self._activity_write = loop.create_task(persist())

        try:
            if asyncio.get_running_loop() is loop:
                schedule_write()
            else:
                loop.call_soon_threadsafe(schedule_write)
        except RuntimeError:
            logger.debug("Skipping device activity write because its event loop is unavailable")

    def _on_audio_fetched(self, successful: bool, duration_ms: int) -> None:
        if successful and self._progress_audio_fetched is not None:
            self._progress_audio_fetched.set()
        self._record_activity(
            "Speaker audio",
            "completed" if successful else "failed",
            duration_ms=duration_ms,
            detail="Audio fetched by speaker" if successful else "Audio expired before fetch",
        )

    def _reset_session(self) -> None:
        self._session_id = None
        self._session_audio.clear()
        self._session_audio_truncated = False
        self._vad = None
        self._vad_ended = False
        self._vad_no_speech_timeout = False
        self._session_is_follow_up = False
        self._session_started_at = 0.0

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


def _is_unintelligible_transcript(transcript: str) -> bool:
    """Recognize empty audio and common Whisper placeholder transcripts only."""
    normalized = re.sub(r"[^a-z0-9]+", " ", transcript.casefold()).strip()
    return not normalized or normalized in UNINTELLIGIBLE_TRANSCRIPTS


def _spoken_tool_status(status: object) -> str:
    """Use a validated planner status, with a generic fallback for other tool paths."""
    if isinstance(status, str):
        normalized = " ".join(status.split()).strip()
        if normalized and len(normalized) <= 120:
            return normalized
    return "I'm checking that now."


def _stable_device_id(mac_address: str | None, host: str) -> str:
    """Prefer the hardware address so DHCP changes do not change device identity."""
    mac = (mac_address or "").strip().lower()
    if mac:
        return mac
    return f"host:{host.strip().lower()}"
