import unittest
from unittest.mock import patch

from app.agent.voice_activity import CaptureEvent, CaptureState, VoiceActivityDetector


FRAME_BYTES = 640


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeVad:
    def is_speech(self, frame: bytes, _sample_rate: int) -> bool:
        return frame[0] == 1


class VoiceActivityDetectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.vad_patcher = patch(
            "app.agent.voice_activity.webrtcvad.Vad", return_value=FakeVad()
        )
        self.vad_patcher.start()
        self.addCleanup(self.vad_patcher.stop)

    def make_detector(self, **options) -> VoiceActivityDetector:
        return VoiceActivityDetector(
            startup_grace_ms=options.pop("startup_grace_ms", 0),
            end_silence_ms=options.pop("end_silence_ms", 40),
            capture_limit_seconds=options.pop("capture_limit_seconds", 20),
            clock=self.clock,
            **options,
        )

    @staticmethod
    def frame(speech: bool) -> bytes:
        return bytes([1 if speech else 0]) + bytes(FRAME_BYTES - 1)

    def test_startup_grace_skips_detection_but_keeps_state_machine_ready(self) -> None:
        detector = self.make_detector(startup_grace_ms=20)

        self.assertEqual([], detector.process(self.frame(True)))
        self.assertIs(detector.state, CaptureState.WAITING_FOR_SPEECH)
        self.assertEqual(
            [CaptureEvent.SPEECH_STARTED], detector.process(self.frame(True) * 3)
        )

    def test_isolated_speech_does_not_restart_windowed_quiet_time(self) -> None:
        detector = self.make_detector(
            end_silence_ms=200,
            activity_history=1,
            active_windows_required=1,
        )

        self.assertEqual(
            [CaptureEvent.SPEECH_STARTED], detector.process(self.frame(True) * 3)
        )
        self.assertEqual([], detector.process(self.frame(True) * 2))
        self.assertEqual([], detector.process(self.frame(False) * 4 + self.frame(True)))
        self.assertEqual([], detector.process(self.frame(True) * 2 + self.frame(False) * 3))
        self.assertEqual([], detector.process(self.frame(False) * 4 + self.frame(True)))
        self.assertEqual(
            [CaptureEvent.SPEECH_ENDED],
            detector.process(self.frame(False) * 4 + self.frame(True)),
        )
        self.assertIs(detector.state, CaptureState.ENDED)

    def test_sustained_activity_keeps_capture_open(self) -> None:
        detector = self.make_detector(end_silence_ms=200)
        self.assertEqual(
            [CaptureEvent.SPEECH_STARTED], detector.process(self.frame(True) * 3)
        )
        self.assertEqual([], detector.process(self.frame(True) * 52))
        self.assertIs(detector.state, CaptureState.SPEAKING)

    def test_brief_speech_uses_continuous_silence_until_activity_is_confirmed(self) -> None:
        detector = self.make_detector(end_silence_ms=200)
        self.assertEqual(
            [CaptureEvent.SPEECH_STARTED], detector.process(self.frame(True) * 3)
        )
        self.assertEqual([], detector.process(self.frame(False) * 9))
        self.assertEqual(
            [CaptureEvent.SPEECH_ENDED], detector.process(self.frame(False))
        )

    def test_zero_capture_limit_is_unlimited_but_speech_timeout_remains(self) -> None:
        detector = self.make_detector(capture_limit_seconds=0)
        self.assertEqual(
            [CaptureEvent.SPEECH_STARTED], detector.process(self.frame(True) * 3)
        )
        self.clock.now = 21
        self.assertEqual([], detector.process(self.frame(True)))
        self.assertIs(detector.state, CaptureState.SPEAKING)

    def test_nonzero_capture_limit_ends_the_session(self) -> None:
        detector = self.make_detector(capture_limit_seconds=2)
        self.clock.now = 2

        self.assertEqual(
            [CaptureEvent.MAX_DURATION_REACHED], detector.process(self.frame(False))
        )

    def test_no_speech_timeout_ends_a_session_without_voice(self) -> None:
        detector = self.make_detector(capture_limit_seconds=0)
        detector.process(self.frame(False))
        self.clock.now = 15

        self.assertEqual(
            [CaptureEvent.NO_SPEECH_TIMEOUT], detector.process(self.frame(False))
        )

    def test_start_gate_confirms_speech_across_sliding_windows(self) -> None:
        detector = self.make_detector(
            start_gate=True,
            window_frames=5,
            window_speech_frames=2,
            activity_history=3,
            active_windows_required=2,
            no_speech_timeout_seconds=3,
        )

        self.assertEqual([], detector.process(self.frame(True) + self.frame(False) * 4))
        self.assertEqual([], detector.process(self.frame(True) * 2 + self.frame(False) * 3))
        self.assertEqual(
            [CaptureEvent.SPEECH_STARTED],
            detector.process(self.frame(True) * 2 + self.frame(False) * 3),
        )

    def test_start_gate_times_out_without_confirmed_speech(self) -> None:
        detector = self.make_detector(
            start_gate=True,
            capture_limit_seconds=0,
            no_speech_timeout_seconds=3,
        )
        self.clock.now = 3

        self.assertEqual(
            [CaptureEvent.NO_SPEECH_TIMEOUT], detector.process(self.frame(False))
        )


if __name__ == "__main__":
    unittest.main()
