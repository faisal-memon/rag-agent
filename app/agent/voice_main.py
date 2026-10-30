"""Standalone FastAPI app for the private ReSpeaker audio transport."""

from fastapi import FastAPI, WebSocket

from app.agent.api.voice import stream_voice

app = FastAPI(
    title="ReSpeaker Voice Transport",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.websocket("/agent/voice/stream")
async def voice_stream(websocket: WebSocket) -> None:
    """Accept direct-device audio on its own listener, separate from the API."""
    await stream_voice(websocket)
