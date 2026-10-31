import io
import wave
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.agent.config import ApiSettings
from app.agent.esphome_voice import ESPHomeVoiceClient


class ESPHomeVoiceClientTest(IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = ESPHomeVoiceClient(
            ApiSettings(
                ESPHOME_DEVICE_HOST="192.0.2.1",
                WHISPERCPP_BASE_URL="http://whisper.test:8080",
            )
        )

    async def test_completed_session_is_wrapped_as_wav_and_transcribed(self):
        pcm = b"\x01\x00\x02\x00"
        await self.client._handle_session_start("conversation-1", 0, None, None)
        await self.client._handle_audio(pcm)

        with (
            patch("app.agent.esphome_voice.transcribe_audio", return_value="hello there") as transcribe,
            self.assertLogs("app.agent.esphome_voice", level="INFO") as logs,
        ):
            await self.client._handle_session_stop(aborted=False)

        transcribe.assert_called_once()
        kwargs = transcribe.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "http://whisper.test:8080")
        self.assertEqual(kwargs["filename"], "respeaker.wav")
        self.assertEqual(kwargs["content_type"], "audio/wav")
        with wave.open(io.BytesIO(kwargs["audio"]), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getframerate(), 16000)
            self.assertEqual(wav_file.readframes(wav_file.getnframes()), pcm)
        self.assertTrue(any("ReSpeaker transcription" in line and "hello there" in line for line in logs.output))
        self.assertIsNone(self.client._session_id)
        self.assertEqual(self.client._session_audio, bytearray())

    async def test_stereo_audio_is_interleaved_for_whisper(self):
        channel_1 = b"\x01\x00\x03\x00"
        channel_2 = b"\x02\x00\x04\x00"
        await self.client._handle_session_start("conversation-stereo", 0, None, None)
        await self.client._handle_audio(channel_1, channel_2)

        with patch("app.agent.esphome_voice.transcribe_audio") as transcribe:
            transcribe.return_value = "stereo"
            await self.client._handle_session_stop(aborted=False)

        with wave.open(io.BytesIO(transcribe.call_args.kwargs["audio"]), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 2)
            self.assertEqual(
                wav_file.readframes(wav_file.getnframes()),
                b"\x01\x00\x02\x00\x03\x00\x04\x00",
            )

    async def test_user_stopped_session_is_transcribed(self):
        await self.client._handle_session_start("conversation-user-stop", 0, None, None)
        await self.client._handle_audio(b"\x01\x00")

        with patch("app.agent.esphome_voice.transcribe_audio", return_value="hello") as transcribe:
            await self.client._handle_session_stop(aborted=True)

        transcribe.assert_called_once()
        self.assertIsNone(self.client._session_id)

    async def test_oversized_session_is_discarded_and_next_session_can_run(self):
        await self.client._handle_session_start("conversation-large", 0, None, None)
        with patch("app.agent.esphome_voice.MAX_SESSION_AUDIO_BYTES", 4):
            await self.client._handle_audio(b"\x01\x00\x02\x00")
            await self.client._handle_audio(b"\x03\x00\x04\x00")

        self.assertTrue(self.client._session_audio_truncated)
        with patch("app.agent.esphome_voice.transcribe_audio") as transcribe:
            await self.client._handle_session_stop(aborted=False)
        transcribe.assert_not_called()
        self.assertIsNone(self.client._session_id)

        await self.client._handle_session_start("conversation-next", 0, None, None)
        await self.client._handle_audio(b"\x01\x00")
        with patch("app.agent.esphome_voice.transcribe_audio", return_value="recovered") as transcribe:
            await self.client._handle_session_stop(aborted=False)
        transcribe.assert_called_once()

    async def test_audio_outside_session_is_ignored(self):
        with patch("app.agent.esphome_voice.transcribe_audio") as transcribe:
            await self.client._handle_audio(b"\x01\x00")

        transcribe.assert_not_called()
        self.assertEqual(self.client._session_audio, bytearray())
