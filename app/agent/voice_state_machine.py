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


_TRANSITIONS: dict[tuple[VoiceModeState, VoiceModeEvent], VoiceModeState] = {
    (VoiceModeState.UNAVAILABLE, VoiceModeEvent.CONNECTION_READY): VoiceModeState.IDLE,
    (VoiceModeState.IDLE, VoiceModeEvent.WAKE_WORD_DETECTED): VoiceModeState.CAPTURING,
    (VoiceModeState.CAPTURING, VoiceModeEvent.CAPTURE_ENDED): VoiceModeState.PROCESSING,
    (VoiceModeState.PROCESSING, VoiceModeEvent.RESPONSE_READY): VoiceModeState.PLAYING,
    (
        VoiceModeState.PLAYING,
        VoiceModeEvent.PLAYBACK_FINISHED_CONTINUE,
    ): VoiceModeState.FOLLOWUP_LISTENING,
    (
        VoiceModeState.PLAYING,
        VoiceModeEvent.PLAYBACK_FINISHED_STOP,
    ): VoiceModeState.COOLDOWN,
    (
        VoiceModeState.FOLLOWUP_LISTENING,
        VoiceModeEvent.FOLLOWUP_STARTED,
    ): VoiceModeState.CAPTURING,
    (
        VoiceModeState.FOLLOWUP_LISTENING,
        VoiceModeEvent.FOLLOWUP_TIMEOUT,
    ): VoiceModeState.COOLDOWN,
    (VoiceModeState.COOLDOWN, VoiceModeEvent.COOLDOWN_EXPIRED): VoiceModeState.IDLE,
    (VoiceModeState.ERROR, VoiceModeEvent.RESET): VoiceModeState.IDLE,
}


class VoiceModeStateMachine:
    """Small event-driven state machine for the ESPHome voice client."""

    def __init__(self, initial: VoiceModeState = VoiceModeState.UNAVAILABLE) -> None:
        self.state = initial

    def dispatch(self, event: VoiceModeEvent) -> VoiceModeTransition:
        """Apply one event and log rejected transitions without changing state."""
        previous = self.state
        if event is VoiceModeEvent.CONNECTION_LOST:
            current = VoiceModeState.UNAVAILABLE
        elif event is VoiceModeEvent.FAILURE:
            current = VoiceModeState.ERROR
        else:
            current = _TRANSITIONS.get((previous, event))
        if current is None:
            logger.warning(
                "Rejected ESPHome voice state transition: %s + %s",
                previous.name,
                event.name,
            )
            return VoiceModeTransition(previous, previous, event, False)
        self.state = current
        logger.info(
            "ESPHome voice state transition: %s + %s -> %s",
            previous.name,
            event.name,
            current.name,
        )
        return VoiceModeTransition(previous, current, event, True)
