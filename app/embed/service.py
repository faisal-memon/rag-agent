import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

from psycopg.types.json import Json

from app.core.chunking import chunk_text
from app.core.config import get_settings
from app.core.db import db_cursor
from app.core.embeddings import embed_texts
from app.core.files import file_checksum

NORMALIZED_DOCUMENTS_DIR = "documents"
NORMALIZED_METADATA_DIR = "metadata"

DOCUMENT_ID_COL = 0
DOCUMENT_PATH_COL = 1
DOCUMENT_FILENAME_COL = 2
DOCUMENT_MIME_TYPE_COL = 3
DOCUMENT_SIZE_BYTES_COL = 4
DOCUMENT_MODIFIED_TIME_COL = 5
DOCUMENT_CHECKSUM_COL = 6
DOCUMENT_INDEXING_VERSION_COL = 7
DOCUMENT_EMBEDDING_MODEL_COL = 8
DOCUMENT_EMBEDDING_TOKENIZER_COL = 9
DOCUMENT_CHUNK_SIZE_COL = 10
DOCUMENT_CHUNK_OVERLAP_COL = 11
DOCUMENT_MISSING_SINCE_COL = 12


def reindex_artifact(path: Path) -> dict:
    started_at = monotonic()
    indexing_strategy = _current_indexing_strategy()
    markdown_path = _markdown_path_from_normalized_artifact(path)

    if markdown_path is None:
        return _artifact_result(scanned_candidates=0, elapsed_seconds=round(monotonic() - started_at, 2))

    _log(
        "Starting artifact reindex",
        artifact_path=str(path),
        markdown_path=str(markdown_path),
        indexing_version=indexing_strategy["indexing_version"],
        embedding_model=indexing_strategy["embedding_model"],
        embedding_tokenizer=indexing_strategy["embedding_tokenizer"],
        chunk_size=indexing_strategy["chunk_size"],
        chunk_overlap=indexing_strategy["chunk_overlap"],
    )

    try:
        if not markdown_path.exists():
            missing_marked_documents = 1 if _mark_missing_for_markdown(markdown_path) else 0
            return _artifact_result(
                missing_marked_documents=missing_marked_documents,
                scanned_candidates=1,
                elapsed_seconds=round(monotonic() - started_at, 2),
            )

        result = _index_markdown_path(markdown_path, indexing_strategy)
        return _artifact_result(
            indexed_documents=result["indexed_documents"],
            indexed_chunks=result["indexed_chunks"],
            skipped_documents=result["skipped_documents"],
            metadata_updated_documents=result["metadata_updated_documents"],
            scanned_candidates=1,
            elapsed_seconds=round(monotonic() - started_at, 2),
        )
    except Exception as exc:
        _log("Artifact indexing error", path=str(markdown_path), error=str(exc), errors=1)
        return _artifact_result(
            scanned_candidates=1,
            elapsed_seconds=round(monotonic() - started_at, 2),
            errors=[{"path": str(markdown_path), "error": str(exc)}],
        )


def reindex_artifacts(paths: list[Path]) -> dict:
    started_at = monotonic()
    indexed_documents = 0
    indexed_chunks = 0
    skipped_documents = 0
    metadata_updated_documents = 0
    missing_marked_documents = 0
    scanned_candidates = 0
    errors: list[dict] = []

    _log(
        "Starting artifact batch reindex",
        artifact_count=len(paths),
    )

    for path in sorted(set(paths)):
        result = reindex_artifact(path)
        indexed_documents += result["indexed_documents"]
        indexed_chunks += result["indexed_chunks"]
        skipped_documents += result["skipped_documents"]
        metadata_updated_documents += result["metadata_updated_documents"]
        missing_marked_documents += result["missing_marked_documents"]
        scanned_candidates += result["scanned_candidates"]
        errors.extend(result["errors"])

    result = {
        "index_source": "normalized",
        "indexed_documents": indexed_documents,
        "indexed_chunks": indexed_chunks,
        "skipped_documents": skipped_documents,
        "metadata_updated_documents": metadata_updated_documents,
        "missing_marked_documents": missing_marked_documents,
        "deleted_documents": 0,
        "scanned_candidates": scanned_candidates,
        "elapsed_seconds": round(monotonic() - started_at, 2),
        "errors": errors,
    }
    _log("Completed artifact reindex", **{k: v for k, v in result.items() if k != "errors"}, errors=len(errors))
    return result


def _artifact_result(
    indexed_documents: int = 0,
    indexed_chunks: int = 0,
    skipped_documents: int = 0,
    metadata_updated_documents: int = 0,
    missing_marked_documents: int = 0,
    scanned_candidates: int = 0,
    elapsed_seconds: float = 0.0,
    errors: list[dict] | None = None,
) -> dict:
    return {
        "index_source": "normalized",
        "indexed_documents": indexed_documents,
        "indexed_chunks": indexed_chunks,
        "skipped_documents": skipped_documents,
        "metadata_updated_documents": metadata_updated_documents,
        "missing_marked_documents": missing_marked_documents,
        "deleted_documents": 0,
        "scanned_candidates": scanned_candidates,
        "elapsed_seconds": elapsed_seconds,
        "errors": errors or [],
    }


def reindex_source() -> dict:
    settings = get_settings()
    scan_dir = _scan_dir()
    scan_dir.mkdir(parents=True, exist_ok=True)
    started_at = monotonic()
    progress_interval = 100
    indexing_strategy = _current_indexing_strategy()

    indexed_documents = 0
    indexed_chunks = 0
    skipped_documents = 0
    metadata_updated_documents = 0
    missing_marked_documents = 0
    deleted_documents = 0
    scanned_candidates = 0
    errors: list[dict] = []
    seen_paths: set[str] = set()

    _log(
        "Starting reindex",
        scan_dir=str(scan_dir),
        enabled_suffixes=".md",
        indexing_version=indexing_strategy["indexing_version"],
        embedding_model=indexing_strategy["embedding_model"],
        embedding_tokenizer=indexing_strategy["embedding_tokenizer"],
        chunk_size=indexing_strategy["chunk_size"],
        chunk_overlap=indexing_strategy["chunk_overlap"],
    )

    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
              id,
              path,
              filename,
              mime_type,
              size_bytes,
              modified_time,
              checksum,
              indexing_version,
              embedding_model,
              embedding_tokenizer,
              chunk_size,
              chunk_overlap,
              missing_since
            FROM documents
            """
        )
        existing_documents = {
            row[DOCUMENT_PATH_COL]: {
                "id": row[DOCUMENT_ID_COL],
                "path": row[DOCUMENT_PATH_COL],
                "filename": row[DOCUMENT_FILENAME_COL],
                "mime_type": row[DOCUMENT_MIME_TYPE_COL],
                "size_bytes": row[DOCUMENT_SIZE_BYTES_COL],
                "modified_time": row[DOCUMENT_MODIFIED_TIME_COL],
                "checksum": row[DOCUMENT_CHECKSUM_COL],
                "indexing_version": row[DOCUMENT_INDEXING_VERSION_COL],
                "embedding_model": row[DOCUMENT_EMBEDDING_MODEL_COL],
                "embedding_tokenizer": row[DOCUMENT_EMBEDDING_TOKENIZER_COL],
                "chunk_size": row[DOCUMENT_CHUNK_SIZE_COL],
                "chunk_overlap": row[DOCUMENT_CHUNK_OVERLAP_COL],
                "missing_since": row[DOCUMENT_MISSING_SINCE_COL],
            }
            for row in cur.fetchall()
        }
        conn.rollback()

    _log("Loaded existing index state", known_documents=len(existing_documents))

    for path in sorted(scan_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        scanned_candidates += 1
        if scanned_candidates == 1 or scanned_candidates % progress_interval == 0:
            _log(
                "Scan progress",
                scanned_candidates=scanned_candidates,
                indexed_documents=indexed_documents,
                skipped_documents=skipped_documents,
                metadata_updated_documents=metadata_updated_documents,
                errors=len(errors),
                current_path=str(path),
            )
        try:
            result = _index_markdown_path(path, indexing_strategy, existing_documents)
            candidate_path = result["path"]
            seen_paths.add(candidate_path)
            indexed_documents += result["indexed_documents"]
            indexed_chunks += result["indexed_chunks"]
            skipped_documents += result["skipped_documents"]
            metadata_updated_documents += result["metadata_updated_documents"]
            if result["indexed_documents"] or result["metadata_updated_documents"]:
                _log(
                    "Processed document",
                    path=candidate_path,
                    indexed_documents=indexed_documents,
                    indexed_chunks=indexed_chunks,
                    skipped_documents=skipped_documents,
                    metadata_updated_documents=metadata_updated_documents,
                )
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
            _log("Indexing error", path=str(path), error=str(exc), errors=len(errors))

    grace_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=settings.embed.missing_grace_days)
    for existing in existing_documents.values():
        if existing["path"] in seen_paths:
            continue
        try:
            with db_cursor() as (conn, cur):
                if existing["missing_since"] is None:
                    cur.execute(
                        """
                        UPDATE documents
                        SET missing_since = NOW(), updated_at = NOW()
                        WHERE id = %s
                        """,
                        (existing["id"],),
                    )
                    conn.commit()
                    missing_marked_documents += 1
                    _log(
                        "Marked document missing",
                        path=existing["path"],
                        missing_marked_documents=missing_marked_documents,
                    )
                elif existing["missing_since"] <= grace_cutoff:
                    cur.execute("DELETE FROM documents WHERE id = %s", (existing["id"],))
                    conn.commit()
                    deleted_documents += 1
                    _log("Deleted stale document", path=existing["path"], deleted_documents=deleted_documents)
        except Exception as exc:
            errors.append({"path": existing["path"], "error": str(exc)})
            _log("Reconcile error", path=existing["path"], error=str(exc), errors=len(errors))

    result = {
        "index_source": "normalized",
        "indexed_documents": indexed_documents,
        "indexed_chunks": indexed_chunks,
        "skipped_documents": skipped_documents,
        "metadata_updated_documents": metadata_updated_documents,
        "missing_marked_documents": missing_marked_documents,
        "deleted_documents": deleted_documents,
        "scanned_candidates": scanned_candidates,
        "elapsed_seconds": round(monotonic() - started_at, 2),
        "errors": errors,
    }
    _log("Completed reindex", **{k: v for k, v in result.items() if k != "errors"}, errors=len(errors))
    return result


def _index_markdown_path(
    markdown_path: Path,
    indexing_strategy: dict,
    existing_documents: dict | None = None,
) -> dict:
    candidate = _normalized_candidate_from_path(markdown_path)
    path_key = candidate["path"]
    modified_time = candidate["modified_time"]
    size_bytes = candidate["size_bytes"]
    mime_type = candidate["mime_type"]
    existing = existing_documents.get(path_key) if existing_documents is not None else _load_existing_document(path_key)

    if _is_unchanged(existing, modified_time, size_bytes, indexing_strategy):
        if existing and existing["missing_since"] is not None:
            _clear_missing(existing["id"])
            existing["missing_since"] = None
        return _index_result(path=path_key, skipped_documents=1)

    checksum = candidate["checksum"]
    if existing and existing["checksum"] == checksum and _has_matching_indexing_strategy(existing, indexing_strategy):
        _update_document_metadata(existing["id"], candidate, modified_time, size_bytes, indexing_strategy)
        existing.update(
            {
                "filename": candidate["filename"],
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "modified_time": modified_time,
                "indexing_version": indexing_strategy["indexing_version"],
                "embedding_model": indexing_strategy["embedding_model"],
                "embedding_tokenizer": indexing_strategy["embedding_tokenizer"],
                "chunk_size": indexing_strategy["chunk_size"],
                "chunk_overlap": indexing_strategy["chunk_overlap"],
                "missing_since": None,
            }
        )
        _log("Updated metadata only", path=path_key)
        return _index_result(path=path_key, metadata_updated_documents=1)

    document_id, chunk_count = _upsert_document(
        candidate=candidate,
        modified_time=modified_time,
        size_bytes=size_bytes,
        checksum=checksum,
        existing=existing,
    )
    if existing_documents is not None:
        existing_documents[path_key] = {
            "id": document_id,
            "path": path_key,
            "filename": candidate["filename"],
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "modified_time": modified_time,
            "checksum": checksum,
            "indexing_version": indexing_strategy["indexing_version"],
            "embedding_model": indexing_strategy["embedding_model"],
            "embedding_tokenizer": indexing_strategy["embedding_tokenizer"],
            "chunk_size": indexing_strategy["chunk_size"],
            "chunk_overlap": indexing_strategy["chunk_overlap"],
            "missing_since": None,
        }
    _log("Indexed document", path=path_key, document_id=document_id, chunk_count=chunk_count)
    return _index_result(path=path_key, indexed_documents=1, indexed_chunks=chunk_count)


def _index_result(
    path: str,
    indexed_documents: int = 0,
    indexed_chunks: int = 0,
    skipped_documents: int = 0,
    metadata_updated_documents: int = 0,
) -> dict:
    return {
        "path": path,
        "indexed_documents": indexed_documents,
        "indexed_chunks": indexed_chunks,
        "skipped_documents": skipped_documents,
        "metadata_updated_documents": metadata_updated_documents,
    }


def _load_existing_document(path: str) -> dict | None:
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT
              id,
              path,
              filename,
              mime_type,
              size_bytes,
              modified_time,
              checksum,
              indexing_version,
              embedding_model,
              embedding_tokenizer,
              chunk_size,
              chunk_overlap,
              missing_since
            FROM documents
            WHERE path = %s
            """,
            (path,),
        )
        row = cur.fetchone()
        conn.rollback()

    if row is None:
        return None
    return {
        "id": row[DOCUMENT_ID_COL],
        "path": row[DOCUMENT_PATH_COL],
        "filename": row[DOCUMENT_FILENAME_COL],
        "mime_type": row[DOCUMENT_MIME_TYPE_COL],
        "size_bytes": row[DOCUMENT_SIZE_BYTES_COL],
        "modified_time": row[DOCUMENT_MODIFIED_TIME_COL],
        "checksum": row[DOCUMENT_CHECKSUM_COL],
        "indexing_version": row[DOCUMENT_INDEXING_VERSION_COL],
        "embedding_model": row[DOCUMENT_EMBEDDING_MODEL_COL],
        "embedding_tokenizer": row[DOCUMENT_EMBEDDING_TOKENIZER_COL],
        "chunk_size": row[DOCUMENT_CHUNK_SIZE_COL],
        "chunk_overlap": row[DOCUMENT_CHUNK_OVERLAP_COL],
        "missing_since": row[DOCUMENT_MISSING_SINCE_COL],
    }


def _clear_missing(document_id: int) -> None:
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE documents
            SET missing_since = NULL, updated_at = NOW()
            WHERE id = %s
            """,
            (document_id,),
        )
        conn.commit()


def _update_document_metadata(
    document_id: int,
    candidate: dict,
    modified_time: datetime,
    size_bytes: int,
    indexing_strategy: dict,
) -> None:
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE documents
            SET filename = %s,
                mime_type = %s,
                size_bytes = %s,
                modified_time = %s,
                indexing_version = %s,
                embedding_model = %s,
                embedding_tokenizer = %s,
                chunk_size = %s,
                chunk_overlap = %s,
                last_indexed_at = NOW(),
                missing_since = NULL,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                candidate["filename"],
                candidate["mime_type"],
                size_bytes,
                modified_time,
                indexing_strategy["indexing_version"],
                indexing_strategy["embedding_model"],
                indexing_strategy["embedding_tokenizer"],
                indexing_strategy["chunk_size"],
                indexing_strategy["chunk_overlap"],
                document_id,
            ),
        )
        conn.commit()


def _is_unchanged(existing: dict | None, modified_time: datetime, size_bytes: int, indexing_strategy: dict) -> bool:
    if not existing:
        return False
    return (
        existing["modified_time"] == modified_time
        and existing["size_bytes"] == size_bytes
        and _has_matching_indexing_strategy(existing, indexing_strategy)
    )


def _has_matching_indexing_strategy(existing: dict, indexing_strategy: dict) -> bool:
    return (
        existing.get("indexing_version") == indexing_strategy["indexing_version"]
        and existing.get("embedding_model") == indexing_strategy["embedding_model"]
        and existing.get("embedding_tokenizer") == indexing_strategy["embedding_tokenizer"]
        and existing.get("chunk_size") == indexing_strategy["chunk_size"]
        and existing.get("chunk_overlap") == indexing_strategy["chunk_overlap"]
    )


def _current_indexing_strategy() -> dict:
    settings = get_settings()
    embed = settings.embed
    return {
        "indexing_version": f"{embed.indexing_version}:normalized",
        "embedding_model": embed.llamacpp_model if embed.provider == "llamacpp" else settings.common.openai_embedding_model,
        "embedding_tokenizer": embed.tokenizer_model_id,
        "chunk_size": embed.chunk_size,
        "chunk_overlap": embed.chunk_overlap,
    }


def _upsert_document(
    candidate: dict,
    modified_time: datetime,
    size_bytes: int,
    checksum: str,
    existing: dict | None,
) -> tuple[int, int]:
    embed = get_settings().embed
    document_path = candidate["path"]
    filename = candidate["filename"]
    _log("Reading normalized document", path=document_path, markdown_path=str(candidate["read_path"]))
    content = candidate["content"]
    mime_type = candidate["mime_type"]
    chunks = chunk_text(filename, content, embed.chunk_size, embed.chunk_overlap)
    for chunk in chunks:
        chunk.metadata.update(candidate.get("chunk_metadata", {}))
    _log("Chunked document", path=document_path, chunk_count=len(chunks))
    if chunks:
        _log("Requesting embeddings", path=document_path, chunk_count=len(chunks))
    embeddings = embed_texts([chunk.content for chunk in chunks], input_type="document") if chunks else []
    if chunks:
        _log("Embeddings ready", path=document_path, embedding_count=len(embeddings))
    indexing_strategy = _current_indexing_strategy()

    with db_cursor() as (conn, cur):
        if existing:
            document_id = existing["id"]
            cur.execute(
                """
                UPDATE documents
                SET filename = %s,
                    mime_type = %s,
                    size_bytes = %s,
                    modified_time = %s,
                    checksum = %s,
                    indexing_version = %s,
                    embedding_model = %s,
                    embedding_tokenizer = %s,
                    chunk_size = %s,
                    chunk_overlap = %s,
                    last_indexed_at = NOW(),
                    missing_since = NULL,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    filename,
                    mime_type,
                    size_bytes,
                    modified_time,
                    checksum,
                    indexing_strategy["indexing_version"],
                    indexing_strategy["embedding_model"],
                    indexing_strategy["embedding_tokenizer"],
                    indexing_strategy["chunk_size"],
                    indexing_strategy["chunk_overlap"],
                    document_id,
                ),
            )
            cur.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
        else:
            cur.execute(
                """
                INSERT INTO documents (
                    path,
                    filename,
                    mime_type,
                    size_bytes,
                    modified_time,
                    checksum,
                    indexing_version,
                    embedding_model,
                    embedding_tokenizer,
                    chunk_size,
                    chunk_overlap,
                    last_indexed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (
                    document_path,
                    filename,
                    mime_type,
                    size_bytes,
                    modified_time,
                    checksum,
                    indexing_strategy["indexing_version"],
                    indexing_strategy["embedding_model"],
                    indexing_strategy["embedding_tokenizer"],
                    indexing_strategy["chunk_size"],
                    indexing_strategy["chunk_overlap"],
                ),
            )
            document_id = cur.fetchone()[0]

        for chunk, embedding in zip(chunks, embeddings):
            cur.execute(
                """
                INSERT INTO chunks (document_id, chunk_index, content, section, page, metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    document_id,
                    chunk.chunk_index,
                    chunk.content,
                    chunk.section,
                    chunk.page,
                    Json(chunk.metadata),
                    embedding,
                ),
            )

        conn.commit()

    return document_id, len(chunks)


def _scan_dir() -> Path:
    return get_settings().common.normalized_output_dir / NORMALIZED_DOCUMENTS_DIR


def _markdown_path_from_normalized_artifact(path: Path) -> Path | None:
    normalized_output_dir = get_settings().common.normalized_output_dir
    documents_dir = normalized_output_dir / NORMALIZED_DOCUMENTS_DIR
    metadata_dir = normalized_output_dir / NORMALIZED_METADATA_DIR

    if path.suffix.lower() == ".md":
        return path
    if path.suffix.lower() != ".json":
        return None

    try:
        relative_path = path.resolve().relative_to(metadata_dir.resolve())
    except ValueError:
        return None
    return documents_dir / relative_path.with_suffix(".md")


def _mark_missing_for_markdown(markdown_path: Path) -> bool:
    metadata = _read_normalized_metadata(_normalized_metadata_path(markdown_path))
    document_path = metadata.get("source_path") or str(markdown_path)
    existing = _load_existing_document(document_path)
    if not existing or existing["missing_since"] is not None:
        return False

    with db_cursor() as (conn, cur):
        cur.execute(
            """
            UPDATE documents
            SET missing_since = NOW(), updated_at = NOW()
            WHERE id = %s
            """,
            (existing["id"],),
        )
        conn.commit()
    _log("Marked document missing", path=document_path)
    return True


def _normalized_candidate_from_path(markdown_path: Path) -> dict:
    metadata_path = _normalized_metadata_path(markdown_path)
    metadata = _read_normalized_metadata(metadata_path)
    source_path = metadata.get("source_path") or str(markdown_path)
    source_name = metadata.get("source_name") or Path(source_path).name or markdown_path.name
    source_stats = _source_stats(Path(source_path))
    markdown_stats = markdown_path.stat()

    return {
        "path": source_path,
        "read_path": markdown_path,
        "filename": source_name,
        "mime_type": metadata.get("mime_type") or "text/markdown",
        "size_bytes": source_stats["size_bytes"] if source_stats else markdown_stats.st_size,
        "modified_time": source_stats["modified_time"]
        if source_stats
        else datetime.fromtimestamp(markdown_stats.st_mtime, tz=timezone.utc),
        "checksum": metadata.get("checksum") or file_checksum(markdown_path),
        "content": markdown_path.read_text(encoding="utf-8", errors="ignore"),
        "chunk_metadata": {
            "index_source": "normalized",
            "markdown_path": str(markdown_path),
            "metadata_path": str(metadata_path),
            "normalization_version": metadata.get("normalization_version"),
            "extraction_backend": metadata.get("extraction_backend"),
            "normalized_at": metadata.get("normalized_at"),
        },
    }


def _normalized_metadata_path(markdown_path: Path) -> Path:
    normalized_output_dir = get_settings().common.normalized_output_dir
    documents_dir = normalized_output_dir / NORMALIZED_DOCUMENTS_DIR
    metadata_dir = normalized_output_dir / NORMALIZED_METADATA_DIR
    try:
        relative_path = markdown_path.resolve().relative_to(documents_dir.resolve())
    except ValueError:
        relative_path = Path(markdown_path.name)
    return metadata_dir / relative_path.with_suffix(".json")


def _read_normalized_metadata(metadata_path: Path) -> dict:
    if not metadata_path.exists():
        return {}
    return json.loads(metadata_path.read_text(encoding="utf-8"))


def _source_stats(source_path: Path) -> dict | None:
    try:
        stats = source_path.stat()
    except OSError:
        return None
    return {
        "size_bytes": stats.st_size,
        "modified_time": datetime.fromtimestamp(stats.st_mtime, tz=timezone.utc),
    }


def _log(message: str, **fields) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    print(f"[rag-worker] {message}" + (f" {details}" if details else ""), flush=True)
