import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from app.normalize.config import get_normalize_settings
from app.core.files import SUPPORTED_SUFFIXES, ParsedDocument, file_checksum, guess_mime_type

NORMALIZATION_VERSION = "normalized-artifacts-v2"
PASSTHROUGH_NORMALIZATION_SUFFIXES = {".txt", ".md"}


@dataclass
class NormalizedArtifact:
    source_path: str
    markdown_path: str
    metadata_path: str
    checksum: str
    mime_type: str
    extraction_backend: str
    normalized_at: str


def normalize_source() -> dict:
    settings = get_normalize_settings()
    source_dir = settings.nextcloud_source_dir
    output_dir = settings.normalized_output_dir
    documents_dir = output_dir / "documents"
    metadata_dir = output_dir / "metadata"
    documents_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    enabled_suffixes = SUPPORTED_SUFFIXES & settings.enabled_suffixes
    normalized = 0
    skipped = 0
    errors: list[dict] = []

    _log("Starting normalization", source_dir=str(source_dir), output_dir=str(output_dir))

    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in enabled_suffixes:
            continue

        try:
            checksum = file_checksum(path)
            artifact_id = _artifact_id(path, checksum)
            markdown_path, metadata_path = _artifact_paths(path, artifact_id, documents_dir, metadata_dir)

            if _artifact_is_current(metadata_path, checksum):
                skipped += 1
                continue

            normalized_document = _normalize_document(path)
            metadata = {
                "artifact_id": artifact_id,
                "source_path": str(path),
                "source_name": path.name,
                "checksum": checksum,
                "mime_type": normalized_document.mime_type or guess_mime_type(path),
                "normalization_version": NORMALIZATION_VERSION,
                "extraction_backend": normalized_document.metadata["parser"],
                "parser_metadata": normalized_document.metadata,
                "normalized_at": datetime.now(timezone.utc).isoformat(),
                "markdown_path": str(markdown_path),
            }

            _write_artifact(markdown_path, normalized_document.content)
            _write_artifact(metadata_path, json.dumps(metadata, indent=2, sort_keys=True))
            normalized += 1

            if normalized == 1 or normalized % 100 == 0:
                _log("Normalization progress", normalized=normalized, skipped=skipped, current_path=str(path))
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
            _log("Normalization error", path=str(path), error=str(exc), errors=len(errors))

    result = {"normalized": normalized, "skipped": skipped, "errors": errors}
    _log("Completed normalization", normalized=normalized, skipped=skipped, errors=len(errors))
    return result


def normalize_file(path: Path) -> NormalizedArtifact:
    output_dir = get_normalize_settings().normalized_output_dir
    documents_dir = output_dir / "documents"
    metadata_dir = output_dir / "metadata"
    documents_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    checksum = file_checksum(path)
    artifact_id = _artifact_id(path, checksum)
    markdown_path, metadata_path = _artifact_paths(path, artifact_id, documents_dir, metadata_dir)
    normalized_document = _normalize_document(path)
    normalized_at = datetime.now(timezone.utc).isoformat()

    _write_artifact(markdown_path, normalized_document.content)
    metadata = {
        "artifact_id": artifact_id,
        "source_path": str(path),
        "source_name": path.name,
        "checksum": checksum,
        "mime_type": normalized_document.mime_type or guess_mime_type(path),
        "normalization_version": NORMALIZATION_VERSION,
        "extraction_backend": normalized_document.metadata["parser"],
        "parser_metadata": normalized_document.metadata,
        "normalized_at": normalized_at,
        "markdown_path": str(markdown_path),
    }
    _write_artifact(metadata_path, json.dumps(metadata, indent=2, sort_keys=True))

    return NormalizedArtifact(
        source_path=str(path),
        markdown_path=str(markdown_path),
        metadata_path=str(metadata_path),
        checksum=checksum,
        mime_type=metadata["mime_type"],
        extraction_backend=metadata["extraction_backend"],
        normalized_at=normalized_at,
    )


def _artifact_is_current(metadata_path: Path, checksum: str) -> bool:
    if not metadata_path.exists():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return metadata.get("checksum") == checksum and metadata.get("normalization_version") == NORMALIZATION_VERSION


def _artifact_id(path: Path, checksum: str) -> str:
    safe_stem = "".join(char if char.isalnum() else "-" for char in path.stem.lower()).strip("-")
    safe_stem = "-".join(part for part in safe_stem.split("-") if part)[:80] or "document"
    return f"{safe_stem}-{checksum[:16]}"


def _artifact_paths(path: Path, artifact_id: str, documents_dir: Path, metadata_dir: Path) -> tuple[Path, Path]:
    relative_parent = _relative_source_parent(path)
    return (
        documents_dir / relative_parent / f"{artifact_id}.md",
        metadata_dir / relative_parent / f"{artifact_id}.json",
    )


def _relative_source_parent(path: Path) -> Path:
    source_dir = get_normalize_settings().nextcloud_source_dir
    try:
        relative_path = path.resolve().relative_to(source_dir.resolve())
    except ValueError:
        return Path()
    return _safe_relative_parent(relative_path.parent)


def _safe_relative_parent(parent: Path) -> Path:
    safe_parts = []
    for part in parent.parts:
        if part in {"", ".", ".."}:
            continue
        safe_parts.append(part.replace("\x00", ""))
    return Path(*safe_parts) if safe_parts else Path()


def _write_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _normalize_document(path: Path) -> ParsedDocument:
    backend = get_normalize_settings().backend.lower().strip()

    if path.suffix.lower() in PASSTHROUGH_NORMALIZATION_SUFFIXES:
        return _normalize_passthrough(path)

    if backend != "docling":
        raise ValueError(f"unsupported normalization backend: {backend}")

    return _normalize_with_docling(path)


def _normalize_with_docling(path: Path) -> ParsedDocument:
    converter = _get_docling_converter()
    result = converter.convert(str(path))
    markdown = result.document.export_to_markdown()
    metadata = {
        "parser": "docling",
        "docling_status": str(getattr(result, "status", "")),
    }
    return ParsedDocument(content=_ensure_markdown_title(path.name, markdown), mime_type=guess_mime_type(path), metadata=metadata)


def _normalize_passthrough(path: Path) -> ParsedDocument:
    content = path.read_text(encoding="utf-8", errors="ignore")
    return ParsedDocument(
        content=_to_markdown(path.name, content),
        mime_type=guess_mime_type(path),
        metadata={"parser": "passthrough"},
    )


@lru_cache(maxsize=1)
def _get_docling_converter():
    try:
        from docling.document_converter import DocumentConverter
    except ModuleNotFoundError as exc:
        raise RuntimeError("Docling is not installed. Install the 'docling' Python package.") from exc

    return DocumentConverter()


def _to_markdown(filename: str, content: str) -> str:
    title = filename.replace("\n", " ").strip()
    body = content.strip()
    return f"# {title}\n\n{body}\n" if body else f"# {title}\n"


def _ensure_markdown_title(filename: str, markdown: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("#"):
        return f"{stripped}\n"
    return _to_markdown(filename, stripped)


def _log(message: str, **fields) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"[rag-normalize] {message}" + (f" {details}" if details else ""), flush=True)
