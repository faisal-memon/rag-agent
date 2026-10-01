import json
import unittest
from unittest.mock import patch

from app.agent.transcription import TranscriptionUnavailableError, transcribe_audio


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class TranscriptionTest(unittest.TestCase):
    def test_sends_browser_audio_to_local_whisper_service(self) -> None:
        with patch("app.agent.transcription.urlopen", return_value=_Response({"text": " hello world "})) as open_url:
            transcript = transcribe_audio(
                base_url="http://whispercpp:8080/",
                audio=b"audio-data",
                filename="recording.webm",
                content_type="audio/webm",
            )

        request = open_url.call_args.args[0]
        self.assertEqual("http://whispercpp:8080/inference", request.full_url)
        self.assertIn(b'name="file"; filename="recording.webm"', request.data)
        self.assertIn(b"audio-data", request.data)
        self.assertIn(b'name="response_format"', request.data)
        self.assertEqual("hello world", transcript)

    def test_rejects_empty_whisper_response(self) -> None:
        with patch("app.agent.transcription.urlopen", return_value=_Response({"text": ""})):
            with self.assertRaisesRegex(TranscriptionUnavailableError, "did not return"):
                transcribe_audio(
                    base_url="http://whispercpp:8080",
                    audio=b"audio-data",
                    filename="recording.webm",
                    content_type="audio/webm",
                )
