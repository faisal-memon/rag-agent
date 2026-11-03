import io
import wave
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.agent.config import ApiSettings
from app.agent.esphome_voice import (
    ESPHomeVoiceClient,
    _is_unintelligible_transcript,
    _spoken_tool_status,
)


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
        self.assertTrue(
            any(
                "ReSpeaker transcription complete" in line and "characters=11" in line
                for line in logs.output
            )
        )
        self.assertIsNone(self.client._session_id)

    def test_unintelligible_transcript_filter_keeps_short_valid_requests(self):
        self.assertTrue(_is_unintelligible_transcript(""))
        self.assertTrue(_is_unintelligible_transcript("[inaudible]"))
        self.assertTrue(_is_unintelligible_transcript("Indistinct"))
        self.assertFalse(_is_unintelligible_transcript("Hi"))

    def test_spoken_tool_status_uses_generic_fallback_when_missing_or_oversized(self):
        self.assertEqual("I'm checking that now.", _spoken_tool_status(None))
        self.assertEqual("I'm checking that now.", _spoken_tool_status("x" * 121))
        self.assertEqual("Checking the forecast.", _spoken_tool_status("  Checking   the forecast. "))

    async def test_unintelligible_transcript_skips_agent_and_speaks_repeat_prompt(self):
        await self.client._handle_session_start("conversation-inaudible", 0, None, None)
        await self.client._handle_audio(b"\x01\x00")
        self.client._agent = object()

        with (
            patch("app.agent.esphome_voice.transcribe_audio", return_value="[inaudible]"),
            patch.object(self.client, "_speak_reply") as speak_reply,
        ):
            await self.client._handle_session_stop(aborted=False)

        speak_reply.assert_awaited_once_with(
            "Sorry, I didn't catch that. Could you please repeat it?",
            "conversation-inaudible",
        )
        self.assertEqual(self.client._session_audio, bytearray())

    async def test_audio_chunks_can_split_pcm_samples_and_secondary_channel_is_ignored(self):
        await self.client._handle_session_start("conversation-chunked", 0, None, None)
        await self.client._handle_audio(b"\x01", b"secondary channel with different chunking")
        await self.client._handle_audio(b"\x00\x02", b"x")
        await self.client._handle_audio(b"\x00")

        with patch("app.agent.esphome_voice.transcribe_audio") as transcribe:
            transcribe.return_value = "mono"
            await self.client._handle_session_stop(aborted=False)

        with wave.open(io.BytesIO(transcribe.call_args.kwargs["audio"]), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.readframes(wav_file.getnframes()), b"\x01\x00\x02\x00")

    async def test_incomplete_final_pcm_sample_is_discarded(self):
        await self.client._handle_session_start("conversation-partial", 0, None, None)
        await self.client._handle_audio(b"\x01\x00\x02")

        with patch("app.agent.esphome_voice.transcribe_audio") as transcribe:
            transcribe.return_value = "mono"
            await self.client._handle_session_stop(aborted=False)

        with wave.open(io.BytesIO(transcribe.call_args.kwargs["audio"]), "rb") as wav_file:
            self.assertEqual(wav_file.readframes(wav_file.getnframes()), b"\x01\x00")

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
