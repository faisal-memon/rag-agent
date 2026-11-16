import unittest

from app.agent.voice_state_machine import (
    VoiceModeEvent,
    VoiceModeState,
    VoiceModeStateMachine,
)


class VoiceModeStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = VoiceModeStateMachine()

    def dispatch(self, *events: VoiceModeEvent) -> None:
        for event in events:
            result = self.machine.dispatch(event)
            self.assertTrue(result.accepted, event)

    def test_normal_request_with_follow_up(self) -> None:
        self.dispatch(
            VoiceModeEvent.CONNECTION_READY,
            VoiceModeEvent.WAKE_WORD_DETECTED,
            VoiceModeEvent.CAPTURE_ENDED,
            VoiceModeEvent.RESPONSE_READY,
            VoiceModeEvent.PLAYBACK_FINISHED_CONTINUE,
            VoiceModeEvent.FOLLOWUP_STARTED,
            VoiceModeEvent.CAPTURE_ENDED,
            VoiceModeEvent.RESPONSE_READY,
            VoiceModeEvent.PLAYBACK_FINISHED_STOP,
            VoiceModeEvent.COOLDOWN_EXPIRED,
        )
        self.assertIs(self.machine.state, VoiceModeState.IDLE)

    def test_follow_up_timeout_returns_to_idle(self) -> None:
        self.dispatch(
            VoiceModeEvent.CONNECTION_READY,
            VoiceModeEvent.WAKE_WORD_DETECTED,
            VoiceModeEvent.CAPTURE_ENDED,
            VoiceModeEvent.RESPONSE_READY,
            VoiceModeEvent.PLAYBACK_FINISHED_CONTINUE,
            VoiceModeEvent.FOLLOWUP_TIMEOUT,
            VoiceModeEvent.COOLDOWN_EXPIRED,
        )
        self.assertIs(self.machine.state, VoiceModeState.IDLE)

    def test_disconnect_from_any_state_clears_to_unavailable(self) -> None:
        self.dispatch(
            VoiceModeEvent.CONNECTION_READY,
            VoiceModeEvent.WAKE_WORD_DETECTED,
            VoiceModeEvent.CONNECTION_LOST,
        )
        self.assertIs(self.machine.state, VoiceModeState.UNAVAILABLE)

    def test_failure_and_reset(self) -> None:
        self.dispatch(VoiceModeEvent.CONNECTION_READY, VoiceModeEvent.FAILURE)
        self.assertIs(self.machine.state, VoiceModeState.ERROR)
        self.dispatch(VoiceModeEvent.RESET)
        self.assertIs(self.machine.state, VoiceModeState.IDLE)

    def test_invalid_transition_is_rejected_without_state_change(self) -> None:
        result = self.machine.dispatch(VoiceModeEvent.RESPONSE_READY)

        self.assertFalse(result.accepted)
        self.assertIs(result.previous, VoiceModeState.UNAVAILABLE)
        self.assertIs(result.current, VoiceModeState.UNAVAILABLE)

    def test_entry_handler_runs_for_accepted_transition(self) -> None:
        entered = []
        machine = VoiceModeStateMachine(entry_handlers={
            VoiceModeState.IDLE: entered.append,
        })

        result = machine.dispatch(VoiceModeEvent.CONNECTION_READY)

        self.assertEqual([result], entered)


if __name__ == "__main__":
    unittest.main()
