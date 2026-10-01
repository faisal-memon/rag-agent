"""Client for the local whisper.cpp transcription service."""

import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TranscriptionUnavailableError(RuntimeError):
    """Raised when the local transcription service cannot accept audio."""


def transcribe_audio(*, base_url: str, audio: bytes, filename: str, content_type: str) -> str:
    """Send one recorded clip to whisper.cpp and return its transcript."""
    body, boundary = _multipart_audio_body(audio, filename, content_type)
    request = Request(
        f"{base_url.rstrip('/')}/inference",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError) as exc:
        raise TranscriptionUnavailableError("Local transcription is unavailable. Start whisper.cpp and try again.") from exc

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise TranscriptionUnavailableError("Local transcription did not return any text. Try recording again.")
    return text.strip()


def _multipart_audio_body(audio: bytes, filename: str, content_type: str) -> tuple[bytes, str]:
    boundary = f"rag-agent-{uuid.uuid4().hex}"
    safe_filename = filename.replace('"', "") or "recording.webm"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    response_format = (
        f"\r\n--{boundary}\r\n"
        'Content-Disposition: form-data; name="response_format"\r\n\r\n'
        "json"
    ).encode()
    return header + audio + response_format + f"\r\n--{boundary}--\r\n".encode(), boundary
