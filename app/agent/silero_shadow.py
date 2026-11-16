"""Optional Silero VAD scorer used only for shadow diagnostics."""

from __future__ import annotations

import logging
from array import array

logger = logging.getLogger(__name__)


class SileroShadowVAD:
    """Score audio without influencing the authoritative WebRTC VAD."""

    def __init__(self, enabled: bool) -> None:
        self._buffer = bytearray()
        self._model = None
        self._torch = None
        self._score: float | None = None
        if not enabled:
            return
        try:
            import torch
            from silero_vad import load_silero_vad

            self._torch = torch
            self._model = load_silero_vad()
        except Exception:
            logger.warning(
                "Silero shadow VAD is enabled but unavailable; install silero-vad and torch",
                exc_info=True,
            )

    @property
    def available(self) -> bool:
        return self._model is not None and self._torch is not None

    def reset(self) -> None:
        self._buffer.clear()
        self._score = None

    def score(self, pcm: bytes) -> float | None:
        """Return the latest probability, updating whenever a 32 ms window is ready."""
        if not self.available:
            return None
        self._buffer.extend(pcm)
        window_bytes = 512 * 2
        if len(self._buffer) < window_bytes:
            return self._score
        window = bytes(self._buffer[:window_bytes])
        del self._buffer[:window_bytes]
        try:
            samples = array("h")
            samples.frombytes(window)
            audio = self._torch.tensor(samples, dtype=self._torch.float32) / 32768.0
            result = self._model(audio, 16000)
            self._score = round(float(result), 5)
        except Exception:
            logger.debug("Silero shadow VAD scoring failed", exc_info=True)
        return self._score
