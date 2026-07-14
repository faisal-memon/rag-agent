from openai import OpenAI

from app.core.config import get_settings
from app.core.db import db_cursor
from app.core.embeddings import embed_texts, get_llm_client

RETRIEVAL_MODE_SEMANTIC = "semantic"
RETRIEVAL_MODE_KEYWORD = "keyword"


def answer_question(question: str, mode: str = RETRIEVAL_MODE_SEMANTIC) -> dict:
    citations = retrieve_chunks(question, mode=mode)

    client, model = get_llm_client()
    answer = _generate_answer(question, citations, client, model)
    return {"answer": answer, "citations": citations}


def retrieve_chunks(question: str, mode: str = RETRIEVAL_MODE_SEMANTIC) -> list[dict]:
    return retrieve_debug(question, mode=mode)["chunks"]


def retrieve_debug(
    question: str,
    mode: str = RETRIEVAL_MODE_SEMANTIC,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    result_limit = limit or get_settings().api.query_limit
    if mode == RETRIEVAL_MODE_KEYWORD:
        rows = _keyword_rows(question, result_limit, offset)
    elif mode == RETRIEVAL_MODE_SEMANTIC:
        question_embedding = embed_texts([question], input_type="query")[0]
        rows = _semantic_rows(question_embedding, result_limit, offset)
    else:
        raise ValueError(f"Unsupported retrieval mode: {mode}")

    return {
        "chunks": _rows_to_chunks(rows, mode),
        "limit": result_limit,
        "offset": offset,
        "total_chunks": int(rows[0][12]) if rows else 0,
        "total_documents": int(rows[0][13]) if rows else 0,
    }


def _keyword_rows(question: str, limit: int, offset: int) -> list[tuple]:
    with db_cursor() as (conn, cur):
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


def _semantic_rows(question_embedding: list[float], limit: int, offset: int) -> list[tuple]:
    with db_cursor() as (conn, cur):
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


def _generate_answer(question: str, citations: list[dict], client: OpenAI, model: str) -> str:
    api = get_settings().api
    if not citations:
        return "I could not find any relevant documents for that question."

    context_blocks = []
    for citation in citations:
        header = f"{citation['filename']} | section={citation['section'] or 'n/a'} | page={citation['page'] or 'n/a'}"
        context_blocks.append(f"{header}\n{citation['content']}")

    prompt = (
        "Answer using ONLY the provided context.\n\n"
        f"Question:\n{question}\n\n"
        "Context:\n"
        + "\n\n---\n\n".join(context_blocks)
        + "\n\nInclude document references in your answer."
    )
    if api.llm_provider == "llamacpp":
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Answer using only the provided context and include document references.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""

    response = client.responses.create(
        model=model,
        input=prompt,
    )
    return response.output_text
