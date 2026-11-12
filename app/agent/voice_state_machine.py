"""Explicit state transitions for the ESPHome voice interaction lifecycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto

logger = logging.getLogger(__name__)


class VoiceModeState(Enum):
    """High-level voice state; connection availability is intentionally separate."""

    UNAVAILABLE = auto()
    IDLE = auto()
    CAPTURING = auto()
    PROCESSING = auto()
    PLAYING = auto()
    FOLLOWUP_LISTENING = auto()
    COOLDOWN = auto()
    ERROR = auto()


class VoiceModeEvent(Enum):
    """Events that may move the voice interaction to another state."""

    CONNECTION_READY = auto()
    CONNECTION_LOST = auto()
    WAKE_WORD_DETECTED = auto()
    CAPTURE_ENDED = auto()
    RESPONSE_READY = auto()
    PLAYBACK_FINISHED_CONTINUE = auto()
    PLAYBACK_FINISHED_STOP = auto()
    FOLLOWUP_STARTED = auto()
    FOLLOWUP_TIMEOUT = auto()
    COOLDOWN_EXPIRED = auto()
    FAILURE = auto()
    RESET = auto()


@dataclass(frozen=True)
class VoiceModeTransition:
    """Result of dispatching one event."""

    previous: VoiceModeState
    current: VoiceModeState
    event: VoiceModeEvent
    accepted: bool


_TRANSITIONS: dict[tuple[VoiceModeState, VoiceModeEvent], VoiceModeState] = dict([
    # The connection is ready, so begin waiting for a wake word.
    ((VoiceModeState.UNAVAILABLE, VoiceModeEvent.CONNECTION_READY),
     VoiceModeState.IDLE),

    # A wake word starts a new audio capture.
    ((VoiceModeState.IDLE, VoiceModeEvent.WAKE_WORD_DETECTED),
     VoiceModeState.CAPTURING),

    # Ended audio is handed to transcription and agent processing.
    ((VoiceModeState.CAPTURING, VoiceModeEvent.CAPTURE_ENDED),
     VoiceModeState.PROCESSING),

    # A completed response moves into speaker playback.
    ((VoiceModeState.PROCESSING, VoiceModeEvent.RESPONSE_READY),
     VoiceModeState.PLAYING),

    # Playback can continue directly into the short follow-up window.
    ((VoiceModeState.PLAYING, VoiceModeEvent.PLAYBACK_FINISHED_CONTINUE),
     VoiceModeState.FOLLOWUP_LISTENING),

    # Playback that does not continue enters the cooldown guard.
    ((VoiceModeState.PLAYING, VoiceModeEvent.PLAYBACK_FINISHED_STOP),
     VoiceModeState.COOLDOWN),

    # Confirmed follow-up speech starts another capture.
    ((VoiceModeState.FOLLOWUP_LISTENING, VoiceModeEvent.FOLLOWUP_STARTED),
     VoiceModeState.CAPTURING),

    # An empty follow-up window ends safely in cooldown.
    ((VoiceModeState.FOLLOWUP_LISTENING, VoiceModeEvent.FOLLOWUP_TIMEOUT),
     VoiceModeState.COOLDOWN),

    # Once the echo-protection cooldown expires, wait for a new wake word.
    ((VoiceModeState.COOLDOWN, VoiceModeEvent.COOLDOWN_EXPIRED),
     VoiceModeState.IDLE),

    # Reset a failed run back to the normal idle state.
    ((VoiceModeState.ERROR, VoiceModeEvent.RESET),
     VoiceModeState.IDLE),
])


def _handle_global_event(event: VoiceModeEvent) -> VoiceModeState | None:
    """Return the state forced by an event that applies from any state."""
    if event is VoiceModeEvent.CONNECTION_LOST:
        return VoiceModeState.UNAVAILABLE
    if event is VoiceModeEvent.FAILURE:
        return VoiceModeState.ERROR
    return None


class VoiceModeStateMachine:
    """Small event-driven state machine for the ESPHome voice client."""

    def __init__(self, initial: VoiceModeState = VoiceModeState.UNAVAILABLE) -> None:
        self.state = initial

    def dispatch(self, event: VoiceModeEvent) -> VoiceModeTransition:
        """Apply one event and log rejected transitions without changing state."""
        previous = self.state

        # Get the next transition for this state and event.
        next_state = _TRANSITIONS.get((previous, event)) or _handle_global_event(event)

        # Reject events that are not valid from the current state.
        if next_state is None:
            logger.warning("Rejected ESPHome voice state transition: %s + %s", previous.name, event.name)
            return VoiceModeTransition(previous, previous, event, False)

        # Commit and report the accepted transition.
        self.state = next_state
        logger.info("ESPHome voice state transition: %s + %s -> %s", previous.name, event.name, next_state.name)

        return VoiceModeTransition(previous, next_state, event, True)
