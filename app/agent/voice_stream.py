"""Protocol helpers for direct voice-device audio streaming.

The first protocol version carries completed push-to-talk utterances as raw
16 kHz, mono, signed 16-bit little-endian PCM. It intentionally does not
transcribe audio or invoke the agent: this module exists to prove the device
audio transport before those later stages are added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import wave


PROTOCOL_VERSION = 1
SAMPLE_RATE_HZ = 16_000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
ENCODING = "pcm_s16le"
MAX_DURATION_SECONDS = 30
MAX_AUDIO_BYTES = SAMPLE_RATE_HZ * CHANNELS * SAMPLE_WIDTH_BYTES * MAX_DURATION_SECONDS


class VoiceProtocolError(ValueError):
    """Raised when a device stream does not follow the supported protocol."""


@dataclass
class VoiceRecording:
    """A bounded in-memory utterance assembled from validated PCM frames."""

    frames: list[bytes] = field(default_factory=list)
    byte_count: int = 0

    def append(self, frame: bytes) -> None:
        if not frame:
            return
        if len(frame) % SAMPLE_WIDTH_BYTES:
            raise VoiceProtocolError("Audio frames must contain whole 16-bit samples.")
        if self.byte_count + len(frame) > MAX_AUDIO_BYTES:
            raise VoiceProtocolError(f"Audio exceeds the {MAX_DURATION_SECONDS}-second limit.")
        self.frames.append(frame)
        self.byte_count += len(frame)

    @property
    def sample_count(self) -> int:
        return self.byte_count // SAMPLE_WIDTH_BYTES

    @property
    def duration_ms(self) -> int:
        return self.sample_count * 1_000 // SAMPLE_RATE_HZ

    def pcm(self) -> bytes:
        return b"".join(self.frames)


def validate_start(message: object) -> None:
    """Validate a version 1 stream-start message."""
    if not isinstance(message, dict):
        raise VoiceProtocolError("Start message must be a JSON object.")
    expected = {
        "type": "start",
        "version": PROTOCOL_VERSION,
        "encoding": ENCODING,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "channels": CHANNELS,
    }
    for field_name, value in expected.items():
        if message.get(field_name) != value:
            raise VoiceProtocolError(f"Unsupported {field_name.replace('_', ' ')}.")


def is_end_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") == "end"


def write_wav(recording: VoiceRecording, path: Path) -> None:
    """Write a validated recording as a standard 16 kHz mono PCM WAV file."""
    with wave.open(str(path), "wb") as output:
        output.setnchannels(CHANNELS)
        output.setsampwidth(SAMPLE_WIDTH_BYTES)
        output.setframerate(SAMPLE_RATE_HZ)
        output.writeframes(recording.pcm())


def completion_message(recording: VoiceRecording) -> dict[str, Any]:
    return {
        "type": "complete",
        "version": PROTOCOL_VERSION,
        "sample_count": recording.sample_count,
        "duration_ms": recording.duration_ms,
    }
