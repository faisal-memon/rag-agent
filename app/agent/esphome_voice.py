"""Voice-session behavior for connected ESPHome devices."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import time
import wave
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import aioesphomeapi

from app.agent import voice_devices
from app.agent.agent import Agent
from app.agent.config import ApiSettings
from app.agent.esphome_devices import ESPHomeDevice
from app.agent.esphome_playback import OneShotAudioServer
from app.agent.piper_service import PiperService
from app.agent.transcription import TranscriptionUnavailableError, transcribe_audio
from app.agent.voice_activity import (
    VOICE_SAMPLE_RATE,
    CaptureEvent as SileroCaptureEvent,
    SileroVoiceActivityDetector,
    VoiceActivityFrame,
)
from app.agent.voice_state_machine import (
    VoiceActivityCoordinator,
    VoiceModeEvent,
    VoiceModeState,
    VoiceModeStateMachine,
    VoiceProgressCoordinator,
    VoiceModeTransition,
)
from app.agent.silero_shadow import SileroShadowVAD

log = logging.getLogger(__name__)

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
}
NON_CONVERSATIONAL_TRANSCRIPTS = {
    "applause",
    "bubble popping",
    "cheering",
    "crowd cheering",
    "door slam",
    "door slams",
    "end playback",
    "laughing",
    "laughter",
    "music",
    "upbeat music",
}
REPEAT_REQUEST = "Sorry, I didn't catch that. Could you please repeat it?"


@dataclass(slots=True)
class VoiceSessionContext:
    """Capture data and VAD state for one ESPHome voice session."""

    conversation_id: str
    started_at: float
    is_follow_up: bool
    audio: bytearray = field(default_factory=bytearray)
    follow_up_audio_grace_bytes_remaining: int = 0
    audio_truncated: bool = False
    vad: SileroVoiceActivityDetector | None = None
    vad_frames: list[VoiceActivityFrame] = field(default_factory=list)
    silero_probability: float | None = None
    vad_ended: bool = False
    no_speech_timeout: bool = False
    last_vad_diagnostic_at: float = 0.0


class ESPHomeVoiceClient:
    """Handle voice sessions for one connected ESPHome device."""

    def __init__(
        self,
        settings: ApiSettings,
        agent: Agent | None = None,
        piper_service: PiperService | None = None,
        *,
        device: ESPHomeDevice | None = None,
    ) -> None:
        # Shared services used throughout the speaker lifecycle.
        self._settings = settings
        self._agent = agent
        self._voice_state_machine = VoiceModeStateMachine(
            entry_handlers={
                VoiceModeState.PROCESSING: self._enter_processing,
                VoiceModeState.PLAYING: self._enter_playing,
            }
        )
        self._piper_service = piper_service or PiperService(settings)
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

        # Production devices create this client after connecting and registering.
        self._device = device
        self._local_client: aioesphomeapi.APIClient | None = None
        self._local_media_player_key: int | None = None
        self._local_device_id: str | None = None
        self._local_device_name = settings.esphome_device_name or "ESPHome speaker"
        self._local_connected = False
        self._media_server_started = False

        # This client is per device, so it owns that device's one active session.
        self._session: VoiceSessionContext | None = None
        self._silero_shadow = SileroShadowVAD(settings.voice_vad_silero_shadow_enabled)

        # Conversation timing and the follow-up request window.
        self._follow_up_pending = False

        # Async helpers initialized when the client starts.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._activity: VoiceActivityCoordinator | None = None
        self._progress_audio_fetched: asyncio.Event | None = None
        self._processing_task: asyncio.Task[None] | None = None
        self._pending_reply: tuple[str, str, bool] | None = None
        self._playback_task: asyncio.Task[None] | None = None

    async def on_device_ready(self) -> None:
        """Subscribe this device's voice callbacks after it connects and registers."""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
            self._activity = VoiceActivityCoordinator(self._loop)
        if self._media_server is not None and not self._media_server_started:
            await self._media_server.start()
            self._media_server_started = True
        if self._device is not None:
            self._device.subscribe_voice_assistant(
                handle_start=self._handle_session_start,
                handle_stop=self._handle_session_stop,
                handle_audio=self._handle_audio,
            )
        self._abort_interrupted_voice_run()
        self._dispatch_voice_event(VoiceModeEvent.CONNECTION_READY)

    async def stop(self) -> None:
        """Stop voice-only resources; the owning device closes the API connection."""
        if self._device is not None:
            self._device.unsubscribe_voice_assistant()
        if self._media_server is not None and self._media_server_started:
            await self._media_server.stop()
            self._media_server_started = False

    async def on_disconnect(self, expected: bool) -> None:
        self._dispatch_voice_event(VoiceModeEvent.CONNECTION_LOST)
        log.info("ESPHome Native API disconnected (expected=%s)", expected)

    @property
    def _client(self) -> aioesphomeapi.APIClient | None:
        """Expose the device API connection to the voice protocol handlers."""
        return self._device.client if self._device is not None else self._local_client

    @_client.setter
    def _client(self, client: aioesphomeapi.APIClient | None) -> None:
        if self._device is not None:
            self._device.client = client
        else:
            self._local_client = client

    @property
    def _media_player_key(self) -> int | None:
        if self._device is not None:
            return self._device.media_player_key
        return self._local_media_player_key

    @_media_player_key.setter
    def _media_player_key(self, key: int | None) -> None:
        if self._device is not None:
            self._device.media_player_key = key
        else:
            self._local_media_player_key = key

    @property
    def _device_id(self) -> str | None:
        return self._device.device_id if self._device is not None else self._local_device_id

    @_device_id.setter
    def _device_id(self, device_id: str | None) -> None:
        if self._device is not None:
            self._device.device_id = device_id
        else:
            self._local_device_id = device_id

    @property
    def _device_name(self) -> str:
        if self._device is not None:
            return self._device.display_name
        return self._local_device_name

    @property
    def _connected(self) -> bool:
        return self._device.connected if self._device is not None else self._local_connected

    def _dispatch_voice_event(self, event: VoiceModeEvent) -> bool:
        """Apply one voice event and report whether the lifecycle accepted it."""
        if not (transition := self._voice_state_machine.dispatch(event)).accepted:
            log.warning(
                "Ignoring ESPHome voice event %s while in %s",
                event.name,
                transition.previous.name,
            )

        return transition.accepted

    def _enter_processing(self, _transition: VoiceModeTransition) -> None:
        """Start transcription and agent processing after capture ends."""
        asyncio.create_task(self._handle_session_stop(False))

    def _enter_playing(self, _transition: VoiceModeTransition) -> None:
        """Start playback for the response prepared by the processing handler."""
        if self._pending_reply is None:
            log.warning("Voice state entered PLAYING without a pending reply")
            return
        answer, conversation_id, continue_conversation = self._pending_reply
        self._pending_reply = None
        self._playback_task = asyncio.create_task(
            self._speak_reply(
                answer,
                conversation_id,
                continue_conversation=continue_conversation,
            )
        )

    async def _handle_session_start(
        self,
        conversation_id: str,
        flags: int,
        audio_settings: aioesphomeapi.VoiceAssistantAudioSettings,
        wake_word_phrase: str | None,
    ) -> int:
        # Determine whether this is a normal capture or a follow-up.
        session_is_follow_up = (
            self._voice_state_machine.state is VoiceModeState.FOLLOWUP_LISTENING
            or (self._follow_up_pending and not wake_word_phrase)
        )
        self._follow_up_pending = False
        follow_up_audio_grace_seconds = (
            self._settings.voice_follow_up_audio_grace_seconds
            if session_is_follow_up
            else 0
        )

        # Create the per-device context used by the following audio and stop callbacks.
        started_at = time.monotonic()
        session = VoiceSessionContext(
            conversation_id=conversation_id,
            started_at=started_at,
            is_follow_up=session_is_follow_up,
            follow_up_audio_grace_bytes_remaining=(
                follow_up_audio_grace_seconds * VOICE_SAMPLE_RATE * VOICE_SAMPLE_WIDTH
            ),
            last_vad_diagnostic_at=started_at,
        )
        self._session = session
        self._silero_shadow.reset()

        # Configure voice activity detection for this capture.
        session.vad = SileroVoiceActivityDetector(
            end_silence_ms=self._settings.voice_end_silence_ms,
            capture_limit_seconds=self._settings.voice_capture_limit_seconds,
            no_speech_timeout_seconds=(
                self._settings.voice_follow_up_window_seconds + follow_up_audio_grace_seconds
                if session.is_follow_up
                else 15
            ),
            on_frame=self._record_vad_frame if self._settings.voice_vad_diagnostics_enabled else None,
        )

        # Notify the state machine that capture has begun.
        session_event = (
            VoiceModeEvent.FOLLOWUP_STARTED
            if session.is_follow_up and not wake_word_phrase
            else VoiceModeEvent.WAKE_WORD_DETECTED
        )
        if not self._dispatch_voice_event(session_event):
            self._reset_session()
            if self._client is not None:
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, None
                )
            return 0
        log.info(
            "ESPHome voice session started (conversation=%s, flags=%s, wake_word=%s, "
            "follow_up=%s, audio_grace_seconds=%d, audio_settings=%s)",
            conversation_id,
            flags,
            wake_word_phrase,
            session.is_follow_up,
            self._settings.voice_follow_up_audio_grace_seconds if session.is_follow_up else 0,
            audio_settings,
        )
        self._record_activity("Voice session", "started")

        # Tell ESPHome that the voice run is active.
        if self._client is not None:
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START, None
            )
            if wake_word_phrase:
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_WAKE_WORD_END,
                    {"wake_word": wake_word_phrase},
                )
        # API_AUDIO sends audio frames on this already-open Native API connection.
        return 0

    async def _handle_audio(self, data: bytes, data2: bytes | None = None) -> None:
        # Ignore audio that arrives outside an active session.
        session = self._session
        if session is None:
            log.debug("Ignoring ESPHome audio frame outside a voice session")
            return

        # Stop accepting audio after the session buffer reaches its limit.
        if session.audio_truncated:
            return

        # Discard initial follow-up audio so playback tail cannot reach VAD or Whisper.
        audio = data
        if session.follow_up_audio_grace_bytes_remaining:
            discarded = min(session.follow_up_audio_grace_bytes_remaining, len(audio))
            session.follow_up_audio_grace_bytes_remaining -= discarded
            audio = audio[discarded:]

        if not audio:
            return

        # Append the usable transport chunk to the current session recording.
        # ESPHome may split PCM samples across Native API messages, and its
        # optional second channel need not have the same chunk boundaries.
        # Use channel 1 (the default voice channel) and join bytes before WAV
        # framing rather than validating each transport frame independently.
        if len(session.audio) + len(audio) > MAX_SESSION_AUDIO_BYTES:
            session.audio_truncated = True
            log.warning(
                "ESPHome audio session exceeded the %d-byte in-memory limit",
                MAX_SESSION_AUDIO_BYTES,
            )
            return
        session.audio.extend(audio)
        session.silero_probability = self._silero_shadow.score(audio)

        # Feed the chunk into voice activity detection.
        self._update_vad(audio)

    def _update_vad(self, data: bytes) -> None:
        """Signal ESPHome when speech begins and ends so its mic stream closes."""
        session = self._session
        if self._client is None or session is None or session.vad_ended or session.vad is None:
            return
        events = session.vad.process(data)
        now = time.monotonic()
        if events or now - session.last_vad_diagnostic_at >= 1.0:
            diagnostic = session.vad.take_diagnostics()
            if diagnostic["frames"]:
                log.info(
                    "ReSpeaker VAD diagnostics (state=%s, frames=%d, speech=%d, "
                    "non_speech=%d, grace=%d, silence_ms=%d, silence_resets=%d, "
                    "longest_non_speech_ms=%d, endpoint_quiet_ms=%d, "
                    "rms_avg=%s, rms_min=%s, rms_max=%s, peak_max=%s)",
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
                )
            session.last_vad_diagnostic_at = now
        for event in events:
            if event is SileroCaptureEvent.SPEECH_STARTED:
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_STT_START, None
                )
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START, None
                )
                log.info("ReSpeaker VAD state: waiting_for_speech -> speaking")
            elif event is SileroCaptureEvent.SPEECH_ENDED:
                session.vad_ended = True
                if not self._dispatch_voice_event(VoiceModeEvent.CAPTURE_ENDED):
                    return
                log.info("ReSpeaker VAD state: waiting_for_silence -> ended")
                self._record_activity(
                    "Capture", "completed", duration_ms=self._elapsed_ms(), detail="Ended after silence"
                )
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END, None
                )
                return
            elif event is SileroCaptureEvent.MAX_DURATION_REACHED:
                session.vad_ended = True
                if not self._dispatch_voice_event(VoiceModeEvent.CAPTURE_ENDED):
                    return
                log.warning("ReSpeaker capture reached its configured time limit")
                self._record_activity(
                    "Capture", "completed", duration_ms=self._elapsed_ms(), detail="Capture limit reached"
                )
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END, None
                )
                return
            elif event is SileroCaptureEvent.NO_SPEECH_TIMEOUT:
                session.vad_ended = True
                session.no_speech_timeout = True
                if not self._dispatch_voice_event(VoiceModeEvent.CAPTURE_ENDED):
                    return
                log.info("ReSpeaker capture ended: no speech detected")
                self._record_activity(
                    "Capture", "info", duration_ms=self._elapsed_ms(), detail="No speech detected"
                )
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END, None
                )
                return

    def update_settings(self, settings: ApiSettings) -> None:
        """Apply settings updates to subsequent voice sessions."""
        self._settings = settings
        self._piper_service.update_settings(settings)
        self._silero_shadow = SileroShadowVAD(settings.voice_vad_silero_shadow_enabled)

    def status(self) -> dict:
        """Return this voice client's device connection details."""
        if self._device is not None:
            return self._device.status()
        return {
            "device_id": self._local_device_id,
            "display_name": self._local_device_name,
            "host": self._settings.esphome_device_host,
            "port": self._settings.esphome_device_port,
            "connected": self._local_connected,
        }

    async def _handle_session_stop(self, aborted: bool) -> None:
        """Run one processing task, ignoring duplicate stop callbacks."""
        if self._processing_task is not None and not self._processing_task.done():
            log.debug("Ignoring duplicate ESPHome session-stop callback")
            return
        self._processing_task = asyncio.create_task(self._process_session_stop(aborted))
        try:
            await self._processing_task
        finally:
            self._processing_task = None

    async def _process_session_stop(self, aborted: bool) -> None:
        speaker_created_at = datetime.now(timezone.utc)
        session = self._session
        capture_seconds = (
            time.monotonic() - session.started_at
            if session is not None
            else 0.0
        )
        conversation_id = session.conversation_id if session is not None else None
        audio = bytes(session.audio) if session is not None else b""
        truncated = session.audio_truncated if session is not None else False
        no_speech_timeout = session.no_speech_timeout if session is not None else False
        session_is_follow_up = session.is_follow_up if session is not None else False
        vad_frames = list(session.vad_frames) if session is not None else []
        if self._voice_state_machine.state is not VoiceModeState.PROCESSING:
            self._dispatch_voice_event(VoiceModeEvent.CAPTURE_ENDED)
        self._reset_session()
        self._write_vad_diagnostics(conversation_id, vad_frames)
        log.info(
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
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END,
                    {
                        "conversation_id": "",
                        "continue_conversation": "0",
                    },
                )
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, None
                )
            if session_is_follow_up:
                self._record_activity(
                    "Follow-up listening",
                    "completed",
                    duration_ms=round(capture_seconds * 1000),
                    detail="No request; returned to wake-word listening",
                )
            self._dispatch_voice_event(VoiceModeEvent.RESET)
            return
        if truncated or not audio:
            self._record_activity(
                "Capture",
                "failed" if truncated else "info",
                duration_ms=round(capture_seconds * 1000),
                detail="Audio buffer limit reached" if truncated else "No audio received",
            )
            self._finish_voice_run()
            return

        if len(audio) % VOICE_SAMPLE_WIDTH:
            log.warning(
                "ESPHome voice session ended with an incomplete PCM sample; "
                "discarding its final byte (conversation=%s)",
                conversation_id,
            )
            audio = audio[:-1]
        if not audio:
            self._finish_voice_run()
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
            log.warning(
                "ReSpeaker transcription failed (conversation=%s): %s",
                conversation_id,
                exc,
            )
            return
        except Exception:
            self._record_activity("Transcription", "failed", duration_ms=self._stage_ms(stage_started))
            log.exception(
                "Unexpected ReSpeaker transcription error (conversation=%s)",
                conversation_id,
            )
            return
        log.info(
            "ReSpeaker transcription complete (conversation=%s, seconds=%.2f, characters=%d)",
            conversation_id,
            time.monotonic() - stage_started,
            len(transcript),
        )
        if _is_non_conversational_transcript(transcript):
            self._record_activity(
                "Transcription",
                "info",
                duration_ms=self._stage_ms(stage_started),
                detail="Non-conversational audio caption ignored",
            )
            log.info(
                "ReSpeaker transcript was a non-conversational audio caption; ending voice run "
                "without an agent request (conversation=%s)",
                conversation_id,
            )
            if vad_ended and self._client is not None:
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
                    {"text": ""},
                )
            self._finish_voice_run()
            return
        if _is_unintelligible_transcript(transcript):
            self._record_activity(
                "Transcription",
                "failed",
                duration_ms=self._stage_ms(stage_started),
                detail="No intelligible speech detected",
            )
            log.info(
                "ReSpeaker transcript was an unintelligible placeholder; skipping agent request "
                "(conversation=%s)",
                conversation_id,
            )
            if vad_ended and self._client is not None:
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
                    {"text": ""},
                )
            if session_is_follow_up:
                self._record_activity(
                    "Follow-up listening",
                    "completed",
                    duration_ms=round(capture_seconds * 1000),
                    detail="Unintelligible audio; ended follow-up window",
                )
                self._finish_voice_run()
                return
            # An initial wake-word request may still receive one helpful retry,
            # but it must not create a follow-up session of its own.
            await self._speak_reply(
                REPEAT_REQUEST,
                conversation_id,
                continue_conversation=False,
            )
            return
        self._record_activity("Transcription", "completed", duration_ms=self._stage_ms(stage_started))

        if vad_ended and self._client is not None:
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
                {"text": transcript},
            )

        if self._agent is None:
            log.debug("ReSpeaker agent is not configured; skipping model request")
            self._finish_voice_run()
            return

        device_id = self._device_id
        if not device_id:
            log.warning("ReSpeaker session has no registered device identity; skipping agent request")
            self._finish_voice_run()
            return
        try:
            history = await asyncio.to_thread(
                voice_devices.load_history,
                device_id,
                VOICE_HISTORY_MAX_MESSAGES,
            )
        except Exception:
            log.exception("Could not load ReSpeaker conversation history")
            history = []
        history.append({"role": "user", "content": transcript})
        stage_started = time.monotonic()
        progress = VoiceProgressCoordinator(
            asyncio.get_running_loop(),
            partial(self._speak_progress, conversation_id=conversation_id),
            _spoken_tool_status,
        )
        progress_task = asyncio.create_task(progress.run())
        result = None
        try:
            self._record_activity("Assistant", "started")
            result = await asyncio.to_thread(
                self._agent.answer,
                transcript,
                history=history,
                on_progress=progress.report,
                response_mode="spoken",
            )
        except Exception:
            self._record_activity("Assistant", "failed", duration_ms=self._stage_ms(stage_started))
            log.exception(
                "ReSpeaker agent request failed (conversation=%s)",
                conversation_id,
            )
        finally:
            if result is None:
                progress_task.cancel()
            else:
                progress.finish()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        if result is None:
            self._dispatch_voice_event(VoiceModeEvent.FAILURE)
            self._finish_voice_run(error="assistant-failed")
            return

        answer = str(result.get("answer") or "").strip()
        if not answer:
            self._record_activity("Assistant", "failed", detail="Empty response")
            log.warning("ReSpeaker agent returned an empty answer (conversation=%s)", conversation_id)
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
            log.exception("Could not save ReSpeaker conversation turn")
        log.info(
            "ReSpeaker agent response complete (conversation=%s, seconds=%.2f, characters=%d)",
            conversation_id,
            time.monotonic() - stage_started,
            len(answer),
        )
        self._record_activity("Assistant", "completed", duration_ms=self._stage_ms(stage_started))

        continue_listening = (
            self._settings.voice_follow_up_window_seconds > 0
            and result.get("continue_listening") is True
        )
        log.info(
            "ReSpeaker follow-up decision (continue_listening=%s, timeout_seconds=%d)",
            continue_listening,
            self._settings.voice_follow_up_window_seconds,
        )
        self._pending_reply = (answer, conversation_id, continue_listening)
        if not self._dispatch_voice_event(VoiceModeEvent.RESPONSE_READY):
            self._pending_reply = None
            self._finish_voice_run(error="invalid-voice-state")
            return
        playback_task = self._playback_task
        if playback_task is not None:
            await playback_task
            self._playback_task = None

    async def _speak_progress(self, message: str, conversation_id: str) -> None:
        """Speak a tool-call update without ending the active voice turn."""
        if self._media_server is None or self._media_player_key is None or self._client is None:
            return
        try:
            flac_audio = await asyncio.wait_for(self._piper_service.synthesize_flac(message), timeout=15)
            self._progress_audio_fetched = asyncio.Event()
            media_url = self._media_server.publish(flac_audio)
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START,
                {"text": message},
            )
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END,
                {"url": media_url},
            )
            log.info(
                "ReSpeaker progress update queued (conversation=%s, message=%r)",
                conversation_id,
                message,
            )
            try:
                await asyncio.wait_for(self._progress_audio_fetched.wait(), timeout=5)
            except asyncio.TimeoutError:
                log.debug("Speaker did not fetch the progress update before timeout")
            await asyncio.sleep(min(4.0, max(1.0, len(message.split()) * 0.42)))
        except Exception:
            log.exception("Could not speak ReSpeaker progress update (conversation=%s)", conversation_id)
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
            log.debug("ReSpeaker playback is disabled; ESPHOME_MEDIA_URL_BASE is unset")
            self._finish_voice_run()
            return
        if self._media_player_key is None or self._client is None:
            self._record_activity(
                "Speaker audio", "failed", detail="Speaker media player is unavailable"
            )
            log.warning("ReSpeaker reply could not be played; Media Player is unavailable")
            self._finish_voice_run()
            return

        try:
            stage_started = time.monotonic()
            self._record_activity("Speech generation", "started")
            flac_audio = await self._piper_service.synthesize_flac(answer)
            log.info(
                "ReSpeaker synthesis complete (conversation=%s, seconds=%.2f, audio_bytes=%d)",
                conversation_id,
                time.monotonic() - stage_started,
                len(flac_audio),
            )
            self._record_activity("Speech generation", "completed", duration_ms=self._stage_ms(stage_started))
            media_url = self._media_server.publish(flac_audio)
            should_continue = (
                self._settings.voice_follow_up_window_seconds > 0
                if continue_conversation is None
                else continue_conversation
            )
            self._follow_up_pending = should_continue
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_START, None
            )
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END,
                {
                    "conversation_id": "",
                    "continue_conversation": "1" if should_continue else "0",
                },
            )
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START,
                {"text": answer},
            )
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END,
                {"url": media_url},
            )
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, None
            )
            self._dispatch_voice_event(
                VoiceModeEvent.PLAYBACK_FINISHED_CONTINUE
                if should_continue
                else VoiceModeEvent.PLAYBACK_FINISHED_STOP
            )
            if not should_continue:
                self._dispatch_voice_event(VoiceModeEvent.COOLDOWN_EXPIRED)
            log.info("ReSpeaker agent reply queued for speaker playback (conversation=%s)", conversation_id)
        except Exception:
            log.exception("ReSpeaker reply synthesis or playback request failed")
            self._record_activity("Speech generation", "failed", duration_ms=self._stage_ms(stage_started))
            self._dispatch_voice_event(VoiceModeEvent.FAILURE)
            self._finish_voice_run()

    def _finish_voice_run(
        self,
        *,
        error: str | None = None,
    ) -> None:
        """End the speaker's active turn after playback cannot be completed."""
        self._follow_up_pending = False
        self._dispatch_voice_event(VoiceModeEvent.RESET)
        self._send_voice_run_end(error)

    def _abort_interrupted_voice_run(self) -> None:
        """Clear protocol state only when reconnect found an interrupted turn."""
        has_active_state = self._voice_state_machine.state not in {
            VoiceModeState.UNAVAILABLE,
            VoiceModeState.IDLE,
        }
        if self._session is None and not has_active_state:
            return
        self._follow_up_pending = False
        self._reset_session()
        self._send_voice_run_end("api-reconnected")

    def _send_voice_run_end(
        self,
        error: str | None = None,
    ) -> None:
        """Tell ESPHome that a protocol voice run has ended, when connected."""
        if self._client is None:
            return
        try:
            if error:
                self._client.send_voice_assistant_event(
                    aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_ERROR,
                    {"code": error, "message": "Voice request ended. Please try again."},
                )
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END,
                {"conversation_id": "", "continue_conversation": "0"},
            )
            self._client.send_voice_assistant_event(
                aioesphomeapi.VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END,
                None,
            )
        except Exception:
            log.exception("Could not send the ReSpeaker voice-session end event")

    def _elapsed_ms(self) -> int:
        session = self._session
        if session is None:
            return 0
        return max(0, round((time.monotonic() - session.started_at) * 1000))

    @staticmethod
    def _stage_ms(started_at: float) -> int:
        return max(0, round((time.monotonic() - started_at) * 1000))

    def _record_activity(
        self, stage: str, status: str, *, duration_ms: int | None = None, detail: str | None = None
    ) -> None:
        device_id = self._device_id
        if not device_id:
            return
        if self._activity is None:
            log.debug("Skipping device activity write because its event loop is unavailable")
            return
        self._activity.record(
            voice_devices.record_activity,
            device_id,
            stage,
            status,
            duration_ms=duration_ms,
            detail=detail,
        )

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
        self._session = None

    def _record_vad_frame(self, frame: VoiceActivityFrame) -> None:
        """Collect frame decisions when temporary VAD diagnostics are enabled."""
        session = self._session
        if session is not None:
            session.vad_frames.append(
                replace(frame, silero_probability=session.silero_probability)
            )

    def _write_vad_diagnostics(
        self, conversation_id: str | None, frames: list[VoiceActivityFrame]
    ) -> None:
        """Write one frame-level CSV without retaining or writing microphone audio."""
        if not self._settings.voice_vad_diagnostics_enabled or not frames:
            return
        try:
            output_dir = Path(self._settings.voice_vad_diagnostics_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", conversation_id or "session")
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output_path = output_dir / f"vad-{safe_id}-{timestamp}.csv"
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=(
                        "frame",
                        "elapsed_ms",
                        "silero_speech",
                        "webrtc_speech",
                        "audio_level_dbfs",
                        "state",
                        "silence_run",
                        "silero_probability",
                    ),
                )
                writer.writeheader()
                for frame in frames:
                    speech = getattr(frame, "silero_speech", getattr(frame, "webrtc_speech", None))
                    writer.writerow(
                        {
                            "frame": frame.frame_index,
                            "elapsed_ms": frame.elapsed_ms,
                            "silero_speech": "" if speech is None else int(speech),
                            "webrtc_speech": "" if speech is None else int(speech),
                            "audio_level_dbfs": frame.audio_level_dbfs,
                            "state": frame.state,
                            "silence_run": frame.silence_run,
                            "silero_probability": getattr(frame, "silero_probability", ""),
                        }
                    )
            log.info(
                "Wrote VAD diagnostics CSV (conversation=%s, frames=%d, path=%s)",
                conversation_id,
                len(frames),
                output_path,
            )
        except Exception:
            log.warning("Could not write VAD diagnostics CSV", exc_info=True)

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


def _is_non_conversational_transcript(transcript: str) -> bool:
    """Recognize Whisper captions for ambient sound, not a spoken request."""
    if re.fullmatch(r"(?:\s*\[[^\]\r\n]{1,120}\]\s*)+", transcript):
        return True
    normalized = re.sub(r"[^a-z0-9]+", " ", transcript.casefold()).strip()
    return normalized in NON_CONVERSATIONAL_TRANSCRIPTS


def _spoken_tool_status(status: object) -> str:
    """Use a validated planner status, with a generic fallback for other tool paths."""
    if isinstance(status, str):
        normalized = " ".join(status.split()).strip()
        if normalized and len(normalized) <= 120:
            return normalized
    return "I'm checking that now."
