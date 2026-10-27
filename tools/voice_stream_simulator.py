"""Send a 16 kHz mono PCM WAV file using the first ReSpeaker stream protocol.

Usage:
    PYTHONPATH=. python tools/voice_stream_simulator.py ws://192.168.1.10:8000/agent/voice/stream sample.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import wave

import websockets

from app.agent.voice_stream import CHANNELS, ENCODING, PROTOCOL_VERSION, SAMPLE_RATE_HZ, SAMPLE_WIDTH_BYTES


FRAME_BYTES = 3_200  # 100 ms at 16 kHz, mono, signed 16-bit PCM.


def read_pcm_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != CHANNELS
            or source.getsampwidth() != SAMPLE_WIDTH_BYTES
            or source.getframerate() != SAMPLE_RATE_HZ
            or source.getcomptype() != "NONE"
        ):
            raise ValueError("Input must be an uncompressed 16 kHz, mono, 16-bit PCM WAV file.")
        return source.readframes(source.getnframes())


async def stream(url: str, pcm: bytes) -> dict:
    async with websockets.connect(url) as socket:
        await socket.send(json.dumps({
            "type": "start",
            "version": PROTOCOL_VERSION,
            "encoding": ENCODING,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "channels": CHANNELS,
        }))
        ready = json.loads(await socket.recv())
        if ready != {"type": "ready", "version": PROTOCOL_VERSION}:
            raise RuntimeError(f"Server did not accept stream: {ready}")
        for offset in range(0, len(pcm), FRAME_BYTES):
            await socket.send(pcm[offset:offset + FRAME_BYTES])
        await socket.send(json.dumps({"type": "end"}))
        return json.loads(await socket.recv())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="WebSocket URL for the voice transport endpoint")
    parser.add_argument("wav", type=Path, help="16 kHz mono 16-bit PCM WAV recording")
    args = parser.parse_args()
    result = asyncio.run(stream(args.url, read_pcm_wav(args.wav)))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
