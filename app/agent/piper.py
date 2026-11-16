"""Wyoming client for Piper text-to-speech."""

from __future__ import annotations

import io
import wave

import soundfile
from wyoming.audio import AudioChunk, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.error import Error
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeVoice

MAX_SYNTHESIS_BYTES = 8_000_000


class PiperClient:
    """Request speech from a Wyoming Piper server and return a FLAC file."""

    def __init__(self, host: str, port: int, voice: str) -> None:
        self._host = host
        self._port = port
        self._voice = voice

    async def list_voices(self) -> list[str]:
        """Return installed Piper voice identifiers advertised by Wyoming."""
        async with AsyncTcpClient(self._host, self._port) as client:
            await client.write_event(Describe().event())
            while True:
                event = await client.read_event()
                if event is None:
                    raise RuntimeError("Piper disconnected while listing voices")
                if Error.is_type(event.type):
                    raise RuntimeError(f"Piper voice discovery failed: {Error.from_event(event)}")
                if Info.is_type(event.type):
                    info = Info.from_event(event)
                    voices = {
                        voice.name
                        for program in info.tts
                        for voice in program.voices
                        if voice.installed
                    }
                    return sorted(voices)

    async def synthesize_flac(self, text: str) -> bytes:
        """Synthesize text as mono FLAC."""
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
