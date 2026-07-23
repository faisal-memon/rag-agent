from app.api.config import get_api_settings
from app.core.db import db_cursor
from app.core.embeddings import embed_texts

RETRIEVAL_MODE_SEMANTIC = "semantic"
RETRIEVAL_MODE_KEYWORD = "keyword"


def search_debug(
    question: str,
    mode: str = RETRIEVAL_MODE_SEMANTIC,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    settings = get_api_settings()
    result_limit = limit or settings.query_limit
    if mode == RETRIEVAL_MODE_KEYWORD:
        rows = _keyword_rows(question, result_limit, offset, settings)
    elif mode == RETRIEVAL_MODE_SEMANTIC:
        question_embedding = embed_texts(
            [question], provider=settings.embedding_provider,
            llamacpp_base_url=settings.embedding_llamacpp_base_url,
            llamacpp_api_key=settings.embedding_llamacpp_api_key,
            llamacpp_model=settings.embedding_llamacpp_model,
            openai_api_key=settings.openai_api_key,
            openai_embedding_model=settings.openai_embedding_model,
            query_prefix=settings.embedding_query_prefix,
            document_prefix=settings.embedding_document_prefix,
            input_type="query",
        )[0]
        rows = _semantic_rows(question_embedding, result_limit, offset, settings)
    else:
        raise ValueError(f"Unsupported retrieval mode: {mode}")

    return {
        "chunks": _rows_to_chunks(rows, mode),
        "limit": result_limit,
        "offset": offset,
        "total_chunks": int(rows[0][12]) if rows else 0,
        "total_documents": int(rows[0][13]) if rows else 0,
    }


def _keyword_rows(question: str, limit: int, offset: int, settings) -> list[tuple]:
    with db_cursor(settings.database) as (conn, cur):
        cur.execute(
            """
            WITH ranked AS (
              SELECT
                c.id,
                c.document_id,
                d.filename,
                d.path,
                c.section,
                c.page,
                c.content,
                ts_rank_cd(c.content_tsvector, plainto_tsquery('english', %s)) AS fts_score,
                0.0::float AS vector_score,
                ts_rank_cd(c.content_tsvector, plainto_tsquery('english', %s)) AS combined_score,
                true AS matched_fts,
                false AS matched_vector
              FROM chunks c
              JOIN documents d ON d.id = c.document_id
              WHERE c.content_tsvector @@ plainto_tsquery('english', %s)
            ),
            totals AS (
              SELECT
                COUNT(*) AS total_chunks,
                COUNT(DISTINCT document_id) AS total_documents
              FROM ranked
            )
            SELECT
              ranked.id,
              ranked.document_id,
              ranked.filename,
              ranked.path,
              ranked.section,
              ranked.page,
              ranked.content,
              ranked.fts_score,
              ranked.vector_score,
              ranked.combined_score,
              ranked.matched_fts,
              ranked.matched_vector,
              totals.total_chunks,
              totals.total_documents
            FROM ranked
            CROSS JOIN totals
            ORDER BY ranked.fts_score DESC
            LIMIT %s
            OFFSET %s
            """,
            (question, question, question, limit, offset),
        )
        rows = cur.fetchall()
        conn.rollback()
    return rows


def _semantic_rows(question_embedding: list[float], limit: int, offset: int, settings) -> list[tuple]:
    with db_cursor(settings.database) as (conn, cur):
        cur.execute(
            """
            WITH totals AS (
              SELECT
                COUNT(*) AS total_chunks,
                COUNT(DISTINCT document_id) AS total_documents
              FROM chunks
              WHERE embedding IS NOT NULL
            ),
            ranked AS (
              SELECT
                c.id,
                c.document_id,
                d.filename,
                d.path,
                c.section,
                c.page,
                c.content,
                0.0::float AS fts_score,
                (1 - (c.embedding <=> %s::vector))::float AS vector_score,
                (1 - (c.embedding <=> %s::vector))::float AS combined_score,
                false AS matched_fts,
                true AS matched_vector
              FROM chunks c
              JOIN documents d ON d.id = c.document_id
              WHERE c.embedding IS NOT NULL
              ORDER BY c.embedding <=> %s::vector
              LIMIT %s
              OFFSET %s
            )
            SELECT
              ranked.id,
              ranked.document_id,
              ranked.filename,
              ranked.path,
              ranked.section,
              ranked.page,
              ranked.content,
              ranked.fts_score,
              ranked.vector_score,
              ranked.combined_score,
              ranked.matched_fts,
              ranked.matched_vector,
              totals.total_chunks,
              totals.total_documents
            FROM ranked
            CROSS JOIN totals
            ORDER BY ranked.vector_score DESC
            """,
            (question_embedding, question_embedding, question_embedding, limit, offset),
        )
        rows = cur.fetchall()
        conn.rollback()
    return rows


def _rows_to_chunks(rows: list[tuple], mode: str) -> list[dict]:
    chunks = []
    for row in rows:
        chunks.append(
            {
                "chunk_id": row[0],
                "filename": row[2],
                "path": row[3],
                "section": row[4],
                "page": row[5],
                "content": row[6],
                "fts_score": float(row[7]),
                "vector_score": float(row[8]),
                "score": float(row[9]),
                "matched_fts": row[10],
                "matched_vector": row[11],
                "retrieval_mode": mode,
            }
        )
    return chunks
