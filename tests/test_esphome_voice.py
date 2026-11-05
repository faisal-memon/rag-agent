import io
import wave
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from aioesphomeapi import VoiceAssistantEventType

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
        self.assertTrue(_is_unintelligible_transcript("Crowd cheering"))
        self.assertTrue(_is_unintelligible_transcript("[upbeat music]"))
        self.assertFalse(_is_unintelligible_transcript("Hi"))

    async def test_follow_up_uses_separate_speech_start_timeout(self):
        self.client._follow_up_pending = True

        await self.client._handle_session_start("follow-up-timeout", 0, None, None)

        self.assertTrue(self.client._session_is_follow_up)
        self.assertEqual(3, self.client._vad._no_speech_timeout_seconds)

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

    async def test_synthesis_failure_stops_the_voice_run_and_disables_follow_up(self):
        self.client._media_server = MagicMock()
        self.client._media_player_key = 1
        self.client._client = MagicMock()
        self.client._piper.synthesize_flac = AsyncMock(side_effect=ConnectionError("Piper disconnected"))
        self.client._follow_up_pending = True

        with self.assertLogs("app.agent.esphome_voice", level="ERROR"):
            await self.client._speak_reply("Hello", "conversation-tts-failure")

        calls = self.client._client.send_voice_assistant_event.call_args_list
        self.assertTrue(
            any(
                call.args[0] is VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END
                and call.args[1]["continue_conversation"] == "0"
                for call in calls
            )
        )
        self.assertTrue(
            any(call.args[0] is VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END for call in calls)
        )
        self.assertFalse(self.client._follow_up_pending)

    def test_activity_write_without_event_loop_does_not_raise(self):
        self.client._device_id = "speaker-test"

        self.client._record_activity("Speech generation", "failed")

        self.assertIsNone(self.client._activity_write)

    async def test_model_failure_ends_speaker_run_without_follow_up(self):
        await self.client._handle_session_start("model-failure", 0, None, None)
        await self.client._handle_audio(b"\x01\x00")
        self.client._client = MagicMock()
        self.client._device_id = "speaker-test"
        self.client._agent = MagicMock()
        self.client._agent.answer.side_effect = ConnectionError("Model restarted")
        self.client._follow_up_pending = True
        with (
            patch("app.agent.esphome_voice.transcribe_audio", return_value="What is the weather?"),
            patch("app.agent.esphome_voice.voice_devices.load_history", return_value=[]),
            self.assertLogs("app.agent.esphome_voice", level="ERROR"),
        ):
            await self.client._handle_session_stop(aborted=False)
        events = [call.args[0] for call in self.client._client.send_voice_assistant_event.call_args_list]
        self.assertIn(VoiceAssistantEventType.VOICE_ASSISTANT_ERROR, events)
        self.assertIn(VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, events)
        self.assertFalse(self.client._follow_up_pending)

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
