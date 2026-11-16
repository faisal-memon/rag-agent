"""Silero-backed streaming voice activity detection."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from enum import Enum, auto
from functools import lru_cache
from typing import Any, Callable

import torch
from silero_vad import VADIterator, load_silero_vad

VOICE_SAMPLE_RATE = 16_000
VOICE_SAMPLE_WIDTH = 2
FRAME_SAMPLES = 512
FRAME_MILLISECONDS = 32
FRAME_BYTES = FRAME_SAMPLES * VOICE_SAMPLE_WIDTH
NO_SPEECH_TIMEOUT_SECONDS = 15


class CaptureState(Enum):
    WAITING_FOR_SPEECH = auto()
    SPEAKING = auto()
    ENDED = auto()


class CaptureEvent(Enum):
    SPEECH_STARTED = auto()
    SPEECH_ENDED = auto()
    MAX_DURATION_REACHED = auto()
    NO_SPEECH_TIMEOUT = auto()


@dataclass(frozen=True)
class VoiceActivityFrame:
    """One Silero decision, suitable for temporary diagnostics."""

    frame_index: int
    elapsed_ms: int
    silero_speech: bool | None
    audio_level_dbfs: float
    state: str
    silence_run: int


@lru_cache(maxsize=1)
def _load_model() -> Any:
    return load_silero_vad()


class SileroVoiceActivityDetector:
    """Turn PCM chunks into speech events using Silero's streaming iterator."""

    def __init__(
        self,
        *,
        end_silence_ms: int,
        capture_limit_seconds: int,
        no_speech_timeout_seconds: int = NO_SPEECH_TIMEOUT_SECONDS,
        on_frame: Callable[[VoiceActivityFrame], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._on_frame = on_frame
        self._started_at = clock()
        self._speech_wait_started_at = self._started_at
        self._capture_limit_seconds = capture_limit_seconds
        self._no_speech_timeout_seconds = no_speech_timeout_seconds
        self._silence_frames = 0
        self._buffer = bytearray()
        self._frame_index = 0
        self._interval_frames = 0
        self._interval_grace_frames = 0
        self._interval_speech_frames = 0
        self._interval_non_speech_frames = 0
        self._interval_rms_sum = 0.0
        self._interval_rms_min: float | None = None
        self._interval_rms_max: float | None = None
        self._interval_peak_max = 0
        self._iterator = VADIterator(
            _load_model(),
            threshold=0.5,
            sampling_rate=VOICE_SAMPLE_RATE,
            min_silence_duration_ms=end_silence_ms,
            speech_pad_ms=30,
        )
        self.state = CaptureState.WAITING_FOR_SPEECH

    def process(self, audio: bytes) -> list[CaptureEvent]:
        """Process available PCM and return Silero capture events."""
        # Buffer incoming bytes until a complete Silero frame is available.
        self._buffer.extend(audio)
        events: list[CaptureEvent] = []

        # Process every complete frame currently in the buffer.
        while len(self._buffer) >= FRAME_BYTES and self.state is not CaptureState.ENDED:
            frame = bytes(self._buffer[:FRAME_BYTES])
            del self._buffer[:FRAME_BYTES]
            now = self._clock()

            # Decode the frame and collect audio-level diagnostics.
            samples = struct.unpack(f"<{FRAME_SAMPLES}h", frame)
            rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
            peak = max(abs(sample) for sample in samples)
            rms_dbfs = 20 * math.log10(rms / 32768.0) if rms > 0 else -120.0
            self._frame_index += 1
            self._interval_frames += 1
            self._interval_rms_sum += rms
            self._interval_rms_min = rms if self._interval_rms_min is None else min(self._interval_rms_min, rms)
            self._interval_rms_max = rms if self._interval_rms_max is None else max(self._interval_rms_max, rms)
            self._interval_peak_max = max(self._interval_peak_max, peak)

            # Stop when the maximum capture duration is reached.
            if self._capture_limit_seconds > 0 and now - self._started_at >= self._capture_limit_seconds:
                self.state = CaptureState.ENDED
                events.append(CaptureEvent.MAX_DURATION_REACHED)
                self._emit_frame(None, now, rms_dbfs)
                break

            # Ask Silero to classify this frame and update its stream state.
            tensor = torch.tensor(samples, dtype=torch.float32) / 32768.0
            result = self._iterator(tensor)
            speech = self._iterator.triggered

            # Track simple diagnostics and the no-speech timeout.
            if speech:
                self._interval_speech_frames += 1
                self._silence_frames = 0
            else:
                self._interval_non_speech_frames += 1
                self._silence_frames += 1

            # Convert Silero boundary results into capture events.
            if result and "start" in result:
                self.state = CaptureState.SPEAKING
                events.append(CaptureEvent.SPEECH_STARTED)
            elif result and "end" in result:
                self.state = CaptureState.ENDED
                events.append(CaptureEvent.SPEECH_ENDED)
            elif self.state is CaptureState.WAITING_FOR_SPEECH and now - self._speech_wait_started_at >= self._no_speech_timeout_seconds:
                self.state = CaptureState.ENDED
                events.append(CaptureEvent.NO_SPEECH_TIMEOUT)
            self._emit_frame(speech, now, rms_dbfs)
        return events

    def _emit_frame(self, speech: bool | None, now: float, rms_dbfs: float) -> None:
        if self._on_frame is None:
            return
        try:
            self._on_frame(
                VoiceActivityFrame(
                    frame_index=self._frame_index,
                    elapsed_ms=max(0, round((now - self._started_at) * 1000)),
                    silero_speech=speech,
                    audio_level_dbfs=round(rms_dbfs, 2),
                    state=self.state.name.lower(),
                    silence_run=self._silence_frames,
                )
            )
        except Exception:
            pass

    def take_diagnostics(self) -> dict[str, int | float | str | None]:
        """Return and reset aggregate audio diagnostics."""
        frames = self._interval_frames
        diagnostics: dict[str, int | float | str | None] = {
            "state": self.state.name.lower(),
            "frames": frames,
            "grace_frames": self._interval_grace_frames,
            "speech_frames": self._interval_speech_frames,
            "non_speech_frames": self._interval_non_speech_frames,
            "silence_ms": self._silence_frames * FRAME_MILLISECONDS,
            "silence_resets": 0,
            "longest_non_speech_ms": self._silence_frames * FRAME_MILLISECONDS,
            "endpoint_quiet_ms": self._silence_frames * FRAME_MILLISECONDS,
            "rms_avg": round(self._interval_rms_sum / frames, 1) if frames else None,
            "rms_min": round(self._interval_rms_min, 1) if self._interval_rms_min is not None else None,
            "rms_max": round(self._interval_rms_max, 1) if self._interval_rms_max is not None else None,
            "peak_max": self._interval_peak_max,
        }
        self._interval_frames = 0
        self._interval_grace_frames = 0
        self._interval_speech_frames = 0
        self._interval_non_speech_frames = 0
        self._interval_rms_sum = 0.0
        self._interval_rms_min = None
        self._interval_rms_max = None
        self._interval_peak_max = 0
        return diagnostics


# Compatibility exports for callers that still exercise the retired WebRTC detector.
SileroCaptureEvent = CaptureEvent
SileroCaptureState = CaptureState
from app.agent.voice_activity_legacy import (  # noqa: E402
    CaptureEvent as LegacyCaptureEvent,
    CaptureState as LegacyCaptureState,
    VoiceActivityDetector,
    VoiceActivityFrame as LegacyVoiceActivityFrame,
)
CaptureEvent = LegacyCaptureEvent
CaptureState = LegacyCaptureState
VoiceActivityFrame = LegacyVoiceActivityFrame
