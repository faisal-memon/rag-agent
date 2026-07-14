import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png"}


@dataclass
class ParsedDocument:
    content: str
    mime_type: str
    metadata: dict


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8192), b""):
            digest.update(block)
    return digest.hexdigest()


def guess_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
