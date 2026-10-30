import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient

from app.agent.api.voice import _verify_wav
from app.agent.voice_main import app
from app.agent.voice_stream import (
    CHANNELS,
    PROTOCOL_VERSION,
    SAMPLE_RATE_HZ,
    SAMPLE_WIDTH_BYTES,
    VoiceProtocolError,
    VoiceRecording,
    write_wav,
)


class VoiceStreamTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_endpoint_accepts_pcm_and_reports_its_duration(self) -> None:
        pcm = b"\x01\x00" * 1_600  # 100 ms of mono 16-bit PCM.
        with self.client.websocket_connect("/agent/voice/stream") as socket:
            socket.send_json({
                "type": "start",
                "version": PROTOCOL_VERSION,
                "encoding": "pcm_s16le",
                "sample_rate_hz": SAMPLE_RATE_HZ,
                "channels": CHANNELS,
            })
            self.assertEqual({"type": "ready", "version": PROTOCOL_VERSION}, socket.receive_json())
            socket.send_bytes(pcm)
            socket.send_json({"type": "end"})
            self.assertEqual(
                {"type": "complete", "version": PROTOCOL_VERSION, "sample_count": 1_600, "duration_ms": 100},
                socket.receive_json(),
            )

    def test_endpoint_rejects_audio_before_start(self) -> None:
        with self.client.websocket_connect("/agent/voice/stream") as socket:
            socket.send_bytes(b"\x00\x00")
            self.assertEqual("invalid_stream", socket.receive_json()["code"])

    def test_recording_wav_preserves_pcm_and_format(self) -> None:
        recording = VoiceRecording()
        pcm = b"\x01\x00\x02\x00"
        recording.append(pcm)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.wav"
            write_wav(recording, path)
            with wave.open(str(path), "rb") as result:
                self.assertEqual(CHANNELS, result.getnchannels())
                self.assertEqual(SAMPLE_WIDTH_BYTES, result.getsampwidth())
                self.assertEqual(SAMPLE_RATE_HZ, result.getframerate())
                self.assertEqual(pcm, result.readframes(result.getnframes()))

    def test_rejects_odd_length_pcm_frame(self) -> None:
        with self.assertRaises(VoiceProtocolError):
            VoiceRecording().append(b"\x00")

    def test_temporary_endpoint_capture_is_removed_after_validation(self) -> None:
        recording = VoiceRecording()
        recording.append(b"\x01\x00")
        observed_paths: list[Path] = []

        def observe_write(_recording: VoiceRecording, path: Path) -> None:
            observed_paths.append(path)
            self.assertTrue(path.exists())

        with patch("app.agent.api.voice.write_wav", side_effect=observe_write):
            import asyncio

            asyncio.run(_verify_wav(recording))

        self.assertEqual(1, len(observed_paths))
        self.assertFalse(observed_paths[0].exists())
