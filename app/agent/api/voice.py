"""Temporary direct-audio WebSocket endpoint for the ReSpeaker transport test."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.agent.config import get_api_settings
from app.agent.transcription import TranscriptionUnavailableError, transcribe_audio
from app.agent.voice_stream import (
    VoiceProtocolError,
    VoiceRecording,
    completion_message,
    is_end_message,
    validate_start,
    write_wav,
)

# Uvicorn's error logger is connected to the container's stdout/stderr stream.
logger = logging.getLogger("uvicorn.error")


async def stream_voice(websocket: WebSocket) -> None:
    """Accept a bounded PCM utterance, acknowledge it, and log its transcript.

    Deployment must keep this route off the public proxy; authentication is
    added before it can reach agent functionality.
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
                    wav_audio = await _verify_wav(recording)
                    await websocket.send_json(completion_message(recording))
                    await websocket.close()
                    await _transcribe_and_log(wav_audio)
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


async def _transcribe_and_log(wav_audio: bytes) -> None:
    """Transcribe one completed utterance through local whisper.cpp."""
    try:
        transcript = await run_in_threadpool(
            transcribe_audio,
            base_url=get_api_settings().whispercpp_base_url,
            audio=wav_audio,
            filename="respeaker.wav",
            content_type="audio/wav",
        )
    except TranscriptionUnavailableError as exc:
        logger.warning("ReSpeaker transcription failed: %s", exc)
    except Exception:
        logger.exception("Unexpected error transcribing ReSpeaker audio")
    else:
        logger.info("ReSpeaker transcription: %s", transcript)


async def _verify_wav(recording: VoiceRecording) -> bytes:
    """Build a temporary WAV payload for Whisper, then remove the file."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary_file:
        capture_path = Path(temporary_file.name)
    try:
        write_wav(recording, capture_path)
        return capture_path.read_bytes()
    finally:
        capture_path.unlink(missing_ok=True)
