"""Piper synthesis and one-shot LAN audio delivery for ESPHome speakers."""

from __future__ import annotations

import asyncio
import io
import logging
import secrets
import time
import wave
from dataclasses import dataclass
from typing import Callable

import soundfile
from wyoming.audio import AudioChunk, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.error import Error
from wyoming.tts import Synthesize, SynthesizeVoice

logger = logging.getLogger(__name__)

MAX_SYNTHESIS_BYTES = 8_000_000
MEDIA_TOKEN_TTL_SECONDS = 60


class PiperClient:
    """Request speech from a Wyoming Piper server and return a FLAC file."""

    def __init__(self, host: str, port: int, voice: str) -> None:
        self._host = host
        self._port = port
        self._voice = voice

    async def synthesize_flac(self, text: str) -> bytes:
        """Synthesize text as mono FLAC for the speaker's announcement pipeline."""
        async with AsyncTcpClient(self._host, self._port) as client:
            await client.write_event(
                Synthesize(
                    text=text,
                    voice=SynthesizeVoice(name=self._voice),
                ).event()
            )
            wav_buffer = io.BytesIO()
            wav_writer: wave.Wave_write | None = None
            total_bytes = 0
            while True:
                event = await client.read_event()
                if event is None:
                    raise RuntimeError("Piper disconnected during speech synthesis")
                if Error.is_type(event.type):
                    raise RuntimeError(f"Piper synthesis failed: {Error.from_event(event)}")
                if AudioStop.is_type(event.type):
                    break
                if not AudioChunk.is_type(event.type):
                    continue

                chunk = AudioChunk.from_event(event)
                total_bytes += len(chunk.audio)
                if total_bytes > MAX_SYNTHESIS_BYTES:
                    raise ValueError("Piper speech exceeded the audio size limit")
                if wav_writer is None:
                    wav_writer = wave.open(wav_buffer, "wb")
                    wav_writer.setframerate(chunk.rate)
                    wav_writer.setsampwidth(chunk.width)
                    wav_writer.setnchannels(chunk.channels)
                wav_writer.writeframes(chunk.audio)

            if wav_writer is None:
                raise RuntimeError("Piper returned no audio")
            wav_writer.close()

        wav_buffer.seek(0)
        pcm, sample_rate = soundfile.read(wav_buffer, dtype="int16", always_2d=True)
        flac_buffer = io.BytesIO()
        soundfile.write(
            flac_buffer,
            pcm,
            sample_rate,
            format="FLAC",
            subtype="PCM_16",
        )
        return flac_buffer.getvalue()


@dataclass
class _AudioItem:
    content: bytes
    expires_at: float
    published_at: float


class OneShotAudioServer:
    """Serve individual generated audio files over a private LAN listener."""

    def __init__(self, host: str, port: int, url_base: str) -> None:
        self._host = host
        self._port = port
        self._url_base = url_base.rstrip("/")
        self._server: asyncio.Server | None = None
        self._items: dict[str, _AudioItem] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._fetch_callback: Callable[[bool, int], None] | None = None

    def set_fetch_callback(self, callback: Callable[[bool, int], None]) -> None:
        """Set a callback invoked after the speaker fetches a complete file."""
        self._fetch_callback = callback

    async def start(self) -> None:
        """Start the dedicated audio-only HTTP listener."""
        self._server = await asyncio.start_server(
            self._handle_request,
            self._host,
            self._port,
            limit=8192,
        )
        self._cleanup_task = asyncio.create_task(self._cleanup_expired())
        logger.info("ESPHome one-shot audio listener started on port %d", self._port)

    async def stop(self) -> None:
        """Stop the listener and discard any pending audio."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._items.clear()

    def publish(self, audio: bytes) -> str:
        """Store one audio response and return its unguessable fetch URL."""
        self._discard_expired()
        token = secrets.token_urlsafe(24)
        self._items[token] = _AudioItem(
            content=audio,
            expires_at=time.monotonic() + MEDIA_TOKEN_TTL_SECONDS,
            published_at=time.monotonic(),
        )
        return f"{self._url_base}/{token}.flac"

    async def _handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        item: _AudioItem | None = None
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            parts = request_line.decode("ascii", "replace").strip().split()
            headers_bytes = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                headers_bytes += len(line)
                if headers_bytes > 8192 or not line:
                    await self._respond(writer, 400, b"bad request")
                    return
                if line in (b"\r\n", b"\n"):
                    break

            if len(parts) != 3 or parts[0] != "GET" or not parts[2].startswith("HTTP/"):
                await self._respond(writer, 405, b"method not allowed")
                return

            path = parts[1]
            if not path.startswith("/") or not path.endswith(".flac") or "/" in path[1:-5]:
                await self._respond(writer, 404, b"not found")
                return
            token = path[1:-5]
            item = self._items.pop(token, None)
            if item is None:
                await self._respond(writer, 404, b"not found")
                return
            if item.expires_at <= time.monotonic():
                self._notify_fetch(False, item)
                await self._respond(writer, 404, b"not found")
                return

            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: audio/flac\r\n"
                + f"Content-Length: {len(item.content)}\r\n".encode("ascii")
                + b"Cache-Control: no-store\r\n"
                + b"Connection: close\r\n\r\n"
                + item.content
            )
            await writer.drain()
            logger.info("ReSpeaker fetched one-shot speech audio (%d bytes)", len(item.content))
            self._notify_fetch(True, item)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            if item is not None:
                self._notify_fetch(False, item)
            logger.debug("ESPHome audio request ended before completion")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _respond(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
    ) -> None:
        reason = {400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}[status]
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
            + b"Content-Type: text/plain\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Cache-Control: no-store\r\n"
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()

    async def _cleanup_expired(self) -> None:
        while True:
            await asyncio.sleep(15)
            self._discard_expired()

    def _discard_expired(self) -> None:
        now = time.monotonic()
        expired = [token for token, item in self._items.items() if item.expires_at <= now]
        for token in expired:
            item = self._items.pop(token, None)
            if item is not None:
                self._notify_fetch(False, item)

    def _notify_fetch(self, successful: bool, item: _AudioItem) -> None:
        if self._fetch_callback is not None:
            elapsed_ms = max(0, round((time.monotonic() - item.published_at) * 1000))
            self._fetch_callback(successful, elapsed_ms)
