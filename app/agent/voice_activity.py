"""Frame-based voice activity and capture endpointing state machine."""

from __future__ import annotations

import math
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
        aggressiveness: int = VAD_AGGRESSIVENESS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._vad = webrtcvad.Vad(aggressiveness)
        self._clock = clock
        self._started_at = clock()
        self._speech_wait_started_at = self._started_at
        self._capture_limit_seconds = capture_limit_seconds
        self._startup_grace_frames = math.ceil(startup_grace_ms / FRAME_MILLISECONDS)
        self._end_silence_frames = math.ceil(end_silence_ms / FRAME_MILLISECONDS)
        self._grace_frames_seen = 0
        self._start_candidate_frames = 0
        self._silence_frames = 0
        self._buffer = bytearray()
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
            now = self._clock()

            if (
                self._capture_limit_seconds > 0
                and now - self._started_at >= self._capture_limit_seconds
            ):
                self.state = CaptureState.ENDED
                events.append(CaptureEvent.MAX_DURATION_REACHED)
                break

            if self.state is CaptureState.STARTUP_GRACE:
                self._grace_frames_seen += 1
                if self._grace_frames_seen >= self._startup_grace_frames:
                    self.state = CaptureState.WAITING_FOR_SPEECH
                    self._speech_wait_started_at = now
                continue

            speech = self._vad.is_speech(frame, VOICE_SAMPLE_RATE)
            if self.state is CaptureState.WAITING_FOR_SPEECH:
                self._start_candidate_frames = (
                    self._start_candidate_frames + 1 if speech else 0
                )
                if self._start_candidate_frames >= START_CONFIRMATION_FRAMES:
                    self.state = CaptureState.SPEAKING
                    events.append(CaptureEvent.SPEECH_STARTED)
                elif now - self._speech_wait_started_at >= NO_SPEECH_TIMEOUT_SECONDS:
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
                    self.state = CaptureState.SPEAKING
                    self._silence_frames = 0
                else:
                    self._silence_frames += 1
                    if self._silence_frames >= self._end_silence_frames:
                        self.state = CaptureState.ENDED
                        events.append(CaptureEvent.SPEECH_ENDED)
        return events
