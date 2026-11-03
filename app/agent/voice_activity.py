"""Frame-based voice activity and capture endpointing state machine."""

from __future__ import annotations

import math
import struct
import time
from enum import Enum, auto
from typing import Callable

import webrtcvad

VOICE_SAMPLE_RATE = 16_000
VOICE_SAMPLE_WIDTH = 2
FRAME_MILLISECONDS = 20
FRAME_BYTES = VOICE_SAMPLE_RATE * VOICE_SAMPLE_WIDTH * FRAME_MILLISECONDS // 1000
START_CONFIRMATION_FRAMES = 3
NO_SPEECH_TIMEOUT_SECONDS = 15
VAD_AGGRESSIVENESS = 2


class CaptureState(Enum):
    STARTUP_GRACE = auto()
    WAITING_FOR_SPEECH = auto()
    SPEAKING = auto()
    WAITING_FOR_SILENCE = auto()
    ENDED = auto()


class CaptureEvent(Enum):
    SPEECH_STARTED = auto()
    SPEECH_ENDED = auto()
    MAX_DURATION_REACHED = auto()
    NO_SPEECH_TIMEOUT = auto()


class VoiceActivityDetector:
    """Turn PCM frames into simple speech-start and capture-end events."""

    def __init__(
        self,
        *,
        startup_grace_ms: int,
        end_silence_ms: int,
        capture_limit_seconds: int,
        no_speech_timeout_seconds: int = NO_SPEECH_TIMEOUT_SECONDS,
        aggressiveness: int = VAD_AGGRESSIVENESS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._vad = webrtcvad.Vad(aggressiveness)
        self._clock = clock
        self._started_at = clock()
        self._speech_wait_started_at = self._started_at
        self._capture_limit_seconds = capture_limit_seconds
        self._no_speech_timeout_seconds = no_speech_timeout_seconds
        self._startup_grace_frames = math.ceil(startup_grace_ms / FRAME_MILLISECONDS)
        self._end_silence_frames = math.ceil(end_silence_ms / FRAME_MILLISECONDS)
        self._grace_frames_seen = 0
        self._start_candidate_frames = 0
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
        self._interval_silence_resets = 0
        self._non_speech_run_frames = 0
        self._interval_longest_non_speech_run_frames = 0
        self._frame_trace: list[dict[str, int | str]] = []
        self._ready_frame_traces: list[dict[str, int | str | float]] = []
        self.state = (
            CaptureState.STARTUP_GRACE
            if self._startup_grace_frames
            else CaptureState.WAITING_FOR_SPEECH
        )

    def process(self, audio: bytes) -> list[CaptureEvent]:
        """Process available PCM and return any state-transition events."""
        self._buffer.extend(audio)
        events: list[CaptureEvent] = []
        while len(self._buffer) >= FRAME_BYTES and self.state is not CaptureState.ENDED:
            frame = bytes(self._buffer[:FRAME_BYTES])
            del self._buffer[:FRAME_BYTES]
            frame_index = self._frame_index
            self._frame_index += 1
            now = self._clock()
            samples = struct.unpack(f"<{FRAME_BYTES // VOICE_SAMPLE_WIDTH}h", frame)
            rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
            peak = max(abs(sample) for sample in samples)
            self._interval_frames += 1
            self._interval_rms_sum += rms
            self._interval_rms_min = rms if self._interval_rms_min is None else min(self._interval_rms_min, rms)
            self._interval_rms_max = rms if self._interval_rms_max is None else max(self._interval_rms_max, rms)
            self._interval_peak_max = max(self._interval_peak_max, peak)

            if (
                self._capture_limit_seconds > 0
                and now - self._started_at >= self._capture_limit_seconds
            ):
                self.state = CaptureState.ENDED
                events.append(CaptureEvent.MAX_DURATION_REACHED)
                break

            if self.state is CaptureState.STARTUP_GRACE:
                self._grace_frames_seen += 1
                self._interval_grace_frames += 1
                if self._grace_frames_seen >= self._startup_grace_frames:
                    self.state = CaptureState.WAITING_FOR_SPEECH
                    self._speech_wait_started_at = now
                self._record_frame_trace(
                    frame_index, "grace", rms, peak, self.state.name.lower()
                )
                continue

            speech = self._vad.is_speech(frame, VOICE_SAMPLE_RATE)
            if speech:
                self._interval_speech_frames += 1
                self._non_speech_run_frames = 0
            else:
                self._interval_non_speech_frames += 1
                self._non_speech_run_frames += 1
                self._interval_longest_non_speech_run_frames = max(
                    self._interval_longest_non_speech_run_frames,
                    self._non_speech_run_frames,
                )
            if self.state is CaptureState.WAITING_FOR_SPEECH:
                self._start_candidate_frames = (
                    self._start_candidate_frames + 1 if speech else 0
                )
                if self._start_candidate_frames >= START_CONFIRMATION_FRAMES:
                    self.state = CaptureState.SPEAKING
                    events.append(CaptureEvent.SPEECH_STARTED)
                elif now - self._speech_wait_started_at >= self._no_speech_timeout_seconds:
                    self.state = CaptureState.ENDED
                    events.append(CaptureEvent.NO_SPEECH_TIMEOUT)
            elif self.state is CaptureState.SPEAKING:
                if not speech:
                    self.state = CaptureState.WAITING_FOR_SILENCE
                    self._silence_frames = 1
                    if self._silence_frames >= self._end_silence_frames:
                        self.state = CaptureState.ENDED
                        events.append(CaptureEvent.SPEECH_ENDED)
            elif self.state is CaptureState.WAITING_FOR_SILENCE:
                if speech:
                    self._interval_silence_resets += 1
                    self.state = CaptureState.SPEAKING
                    self._silence_frames = 0
                else:
                    self._silence_frames += 1
                    if self._silence_frames >= self._end_silence_frames:
                        self.state = CaptureState.ENDED
                        events.append(CaptureEvent.SPEECH_ENDED)
            self._record_frame_trace(
                frame_index,
                "speech" if speech else "non_speech",
                rms,
                peak,
                self.state.name.lower(),
            )
        return events

    def _record_frame_trace(
        self,
        frame_index: int,
        decision: str,
        rms: float,
        peak: int,
        state: str,
    ) -> None:
        """Retain only per-frame metadata, never the frame's audio samples."""
        self._ready_frame_traces.append(
            {
                "start_ms": frame_index * FRAME_MILLISECONDS,
                "decision": decision,
                "rms_dbfs": round(20 * math.log10(max(1.0, rms) / 32768), 1),
                "peak_percent": round(100 * peak / 32767),
                "state": state,
                "silence_ms": self._silence_frames * FRAME_MILLISECONDS,
            }
        )

    def take_frame_diagnostics(self) -> list[dict[str, int | str | float]]:
        """Return per-frame VAD decisions; audio itself is never retained."""
        diagnostics = self._ready_frame_traces
        self._ready_frame_traces = []
        return diagnostics

    def take_diagnostics(self) -> dict[str, int | float | str | None]:
        """Return and reset one interval of aggregate VAD/audio-level diagnostics."""
        frames = self._interval_frames
        diagnostics: dict[str, int | float | str | None] = {
            "state": self.state.name.lower(),
            "frames": frames,
            "grace_frames": self._interval_grace_frames,
            "speech_frames": self._interval_speech_frames,
            "non_speech_frames": self._interval_non_speech_frames,
            "silence_ms": self._silence_frames * FRAME_MILLISECONDS,
            "silence_resets": self._interval_silence_resets,
            "longest_non_speech_ms": self._interval_longest_non_speech_run_frames * FRAME_MILLISECONDS,
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
        self._interval_silence_resets = 0
        self._interval_longest_non_speech_run_frames = 0
        return diagnostics
