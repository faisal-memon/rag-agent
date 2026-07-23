from app.api.config import get_api_settings
from app.core.db import db_cursor


def pipeline_status(limit: int = 10) -> dict:
    with db_cursor(get_api_settings().database) as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM documents")
        document_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM chunks")
        chunk_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM documents WHERE missing_since IS NOT NULL")
        missing_document_count = cur.fetchone()[0]

        cur.execute(
            """
            SELECT
              d.id,
              d.path,
              d.filename,
              d.mime_type,
              d.size_bytes,
              d.modified_time,
              d.last_indexed_at,
              d.missing_since,
              d.indexing_version,
              d.embedding_model,
              COUNT(c.id) AS chunk_count
            FROM documents d
            LEFT JOIN chunks c ON c.document_id = d.id
            GROUP BY d.id
            ORDER BY COALESCE(d.last_indexed_at, d.updated_at, d.created_at) DESC
            LIMIT %s
            """,
            (limit,),
        )
        recent_documents = [_document_row(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT
              d.id,
              d.path,
              d.filename,
              d.mime_type,
              d.size_bytes,
              d.modified_time,
              d.last_indexed_at,
              d.missing_since,
              d.indexing_version,
              d.embedding_model,
              COUNT(c.id) AS chunk_count
            FROM documents d
            LEFT JOIN chunks c ON c.document_id = d.id
            WHERE d.missing_since IS NOT NULL
            GROUP BY d.id
            ORDER BY d.missing_since DESC
            LIMIT %s
            """,
            (limit,),
        )
        missing_documents = [_document_row(row) for row in cur.fetchall()]

        cur.execute(
            """
            SELECT
              indexing_version,
              embedding_model,
              embedding_tokenizer,
              chunk_size,
              chunk_overlap,
              COUNT(*) AS document_count
            FROM documents
            GROUP BY indexing_version, embedding_model, embedding_tokenizer, chunk_size, chunk_overlap
            ORDER BY document_count DESC, indexing_version
            """
        )
        indexing_strategies = [
            {
                "indexing_version": row[0],
                "embedding_model": row[1],
                "embedding_tokenizer": row[2],
                "chunk_size": row[3],
                "chunk_overlap": row[4],
                "document_count": row[5],
            }
            for row in cur.fetchall()
        ]
        conn.rollback()

    return {
        "document_count": document_count,
        "chunk_count": chunk_count,
        "missing_document_count": missing_document_count,
        "recent_documents": recent_documents,
        "missing_documents": missing_documents,
        "indexing_strategies": indexing_strategies,
    }


def _document_row(row: tuple) -> dict:
    return {
        "id": row[0],
        "path": row[1],
        "filename": row[2],
        "mime_type": row[3],
        "size_bytes": row[4],
        "modified_time": row[5],
        "last_indexed_at": row[6],
        "missing_since": row[7],
        "indexing_version": row[8],
        "embedding_model": row[9],
        "chunk_count": row[10],
    }
