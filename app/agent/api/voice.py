"""Temporary direct-audio WebSocket endpoint for the ReSpeaker transport test."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from app.agent.voice_stream import (
    VoiceProtocolError,
    VoiceRecording,
    completion_message,
    is_end_message,
    validate_start,
    write_wav,
)


async def stream_voice(websocket: WebSocket) -> None:
    """Accept one bounded PCM utterance and acknowledge its reconstructed WAV.

    This is a transport-only test endpoint. The temporary WAV verifies that the
    incoming frames form valid audio, then is removed before the client gets a
    completion acknowledgement. Deployment must keep this route off the public
    proxy; authentication is added before it can reach agent functionality.
    """
    await websocket.accept()
    recording: VoiceRecording | None = None

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

            text = message.get("text")
            binary = message.get("bytes")
            if text is not None:
                try:
                    control = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise VoiceProtocolError("Control messages must be valid JSON.") from exc

                if recording is None:
                    validate_start(control)
                    recording = VoiceRecording()
                    await websocket.send_json({"type": "ready", "version": 1})
                    continue

                if is_end_message(control):
                    if recording.byte_count == 0:
                        raise VoiceProtocolError("Audio stream was empty.")
                    await _verify_wav(recording)
                    await websocket.send_json(completion_message(recording))
                    await websocket.close()
                    return
                raise VoiceProtocolError("Expected binary audio frames or an end message.")

            if binary is None:
                raise VoiceProtocolError("Expected a control message or binary audio frame.")
            if recording is None:
                raise VoiceProtocolError("Send a start message before audio frames.")
            recording.append(binary)
    except WebSocketDisconnect:
        return
    except VoiceProtocolError as exc:
        await websocket.send_json({"type": "error", "code": "invalid_stream", "message": str(exc)})
        await websocket.close(code=1008)


async def _verify_wav(recording: VoiceRecording) -> None:
    """Exercise WAV creation and ensure temporary capture data is always removed."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary_file:
        capture_path = Path(temporary_file.name)
    try:
        write_wav(recording, capture_path)
    finally:
        capture_path.unlink(missing_ok=True)
