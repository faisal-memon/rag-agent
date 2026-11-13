"""Explicit state transitions for the ESPHome voice interaction lifecycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

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


# Each state owns its normal event transitions so the lifecycle reads state by state.
_TRANSITIONS: dict[VoiceModeState, dict[VoiceModeEvent, VoiceModeState]] = {
    # The connection is ready, so begin waiting for a wake word.
    VoiceModeState.UNAVAILABLE: {
        VoiceModeEvent.CONNECTION_READY: VoiceModeState.IDLE,
    },
    # A wake word starts a new audio capture.
    VoiceModeState.IDLE: {
        VoiceModeEvent.WAKE_WORD_DETECTED: VoiceModeState.CAPTURING,
    },
    # Ended audio is handed to transcription and agent processing.
    VoiceModeState.CAPTURING: {
        VoiceModeEvent.CAPTURE_ENDED: VoiceModeState.PROCESSING,
    },
    # A completed response moves into speaker playback.
    VoiceModeState.PROCESSING: {
        VoiceModeEvent.RESPONSE_READY: VoiceModeState.PLAYING,
    },
    # Playback can continue directly into the short follow-up window.
    VoiceModeState.PLAYING: {
        VoiceModeEvent.PLAYBACK_FINISHED_CONTINUE: VoiceModeState.FOLLOWUP_LISTENING,
        VoiceModeEvent.PLAYBACK_FINISHED_STOP: VoiceModeState.COOLDOWN,
    },
    # Follow-up speech or timeout ends the short continuation window.
    VoiceModeState.FOLLOWUP_LISTENING: {
        VoiceModeEvent.FOLLOWUP_STARTED: VoiceModeState.CAPTURING,
        VoiceModeEvent.FOLLOWUP_TIMEOUT: VoiceModeState.COOLDOWN,
    },
    # Once the echo-protection cooldown expires, wait for a new wake word.
    VoiceModeState.COOLDOWN: {
        VoiceModeEvent.COOLDOWN_EXPIRED: VoiceModeState.IDLE,
    },
    VoiceModeState.ERROR: {},
}

# Side effects are registered separately from transitions so the table stays declarative.
VoiceModeHandler = Callable[["VoiceModeTransition"], None]
_ENTRY_HANDLERS: dict[VoiceModeState, VoiceModeHandler] = {}


@dataclass(frozen=True)
class VoiceModeTransition:
    """Result of dispatching one event."""

    previous: VoiceModeState
    current: VoiceModeState
    event: VoiceModeEvent
    accepted: bool


class VoiceModeStateMachine:
    """Small event-driven state machine for the ESPHome voice client."""

    def __init__(
        self,
        initial: VoiceModeState = VoiceModeState.UNAVAILABLE,
        entry_handlers: dict[VoiceModeState, VoiceModeHandler] | None = None,
    ) -> None:
        self.state = initial
        self._entry_handlers = entry_handlers or _ENTRY_HANDLERS

    def dispatch(self, event: VoiceModeEvent) -> VoiceModeTransition:
        """Apply one event and log rejected transitions without changing state."""
        previous = self.state

        # Get the next transition for this state and event.
        next_state = _TRANSITIONS.get(previous, {}).get(event) or _handle_global_event(event)

        # Reject events that are not valid from the current state.
        if next_state is None:
            logger.warning("Rejected ESPHome voice state transition: %s + %s", previous.name, event.name)
            return VoiceModeTransition(
                previous=previous,
                current=previous,
                event=event,
                accepted=False,
            )

        # Commit and report the accepted transition.
        self.state = next_state
        logger.info("ESPHome voice state transition: %s + %s -> %s", previous.name, event.name, next_state.name)

        transition = VoiceModeTransition(
            previous=previous,
            current=next_state,
            event=event,
            accepted=True,
        )
        handler = self._entry_handlers.get(next_state)
        if handler is not None:
            handler(transition)
        return transition


def _handle_global_event(event: VoiceModeEvent) -> VoiceModeState | None:
    """Return the state forced by an event that applies from any state."""
    if event is VoiceModeEvent.CONNECTION_LOST:
        return VoiceModeState.UNAVAILABLE
    if event is VoiceModeEvent.FAILURE:
        return VoiceModeState.ERROR
    if event is VoiceModeEvent.RESET:
        return VoiceModeState.IDLE
    return None
