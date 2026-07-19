import inspect
import re
from pathlib import Path
from typing import Any

from app.api.retrieval import RETRIEVAL_MODE_KEYWORD, RETRIEVAL_MODE_SEMANTIC, retrieve_debug
from app.api.agent.memory import read_memory, remember
from app.core.config import get_settings
from app.core.db import db_cursor

DEFAULT_DOCUMENT_LIMIT = 8
DEFAULT_CHUNK_LIMIT = 8
DEFAULT_DOCUMENT_CHARS = 12000
DEFAULT_DOCUMENT_LINES = 200
DEFAULT_GREP_LIMIT = 12
DEFAULT_GREP_CONTEXT_CHARS = 240

AGENT_TOOL_NAMES = (
    "search_documents",
    "keyword_search",
    "semantic_search",
    "grep_documents",
    "read_document",
    "remember",
)
RETRIEVAL_TOOL_NAMES = (
    "search_documents",
    "keyword_search",
    "semantic_search",
    "grep_documents",
)


def search_documents(
    query: str = "",
    path_prefix: str | None = None,
    limit: int = DEFAULT_DOCUMENT_LIMIT,
) -> list[dict]:
    """Find candidate source documents by filename, path, and indexed metadata.

    Args:
        query: Whitespace-separated terms to match against source filename/path. Use names, dates,
            directory words, vendors, file labels, or leave blank when filtering only by path_prefix.
        path_prefix: Optional source document path or directory prefix, such as /documents/Vehicles.
            This is a source path, not a normalized Markdown path.
        limit: Maximum number of documents to return.
    """
    query = query.strip()
    limit = _bounded_limit(limit)
    conditions = ["missing_since IS NULL"]
    params: list[Any] = []

    if query:
        terms = [term for term in query.split() if term]
        for term in terms:
            conditions.append("(filename ILIKE %s OR path ILIKE %s)")
            pattern = f"%{term}%"
            params.extend([pattern, pattern])

    if path_prefix:
        conditions.append("path ILIKE %s")
        params.append(f"{path_prefix.rstrip('/')}%")

    params.append(limit)
    where_clause = " AND ".join(conditions)

    with db_cursor() as (conn, cur):
        cur.execute(
            f"""
            SELECT
              id,
              path,
              filename,
              mime_type,
              size_bytes,
              modified_time,
              last_indexed_at,
              indexing_version,
              embedding_model
            FROM documents
            WHERE {where_clause}
            ORDER BY modified_time DESC, last_indexed_at DESC NULLS LAST
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
        conn.rollback()

    return [
        {
            "document_id": row[0],
            "path": row[1],
            "filename": row[2],
            "mime_type": row[3],
            "size_bytes": row[4],
            "modified_time": row[5].isoformat() if row[5] else None,
            "last_indexed_at": row[6].isoformat() if row[6] else None,
            "indexing_version": row[7],
            "embedding_model": row[8],
        }
        for row in rows
    ]


def keyword_search(query: str, limit: int = DEFAULT_CHUNK_LIMIT) -> list[dict]:
    """Search indexed chunks with PostgreSQL full-text search for exact language.

    Args:
        query: Keyword-style phrase for exact words, names, dates, dollar amounts, identifiers, and
            expanded acronyms such as AGI plus adjusted gross income.
        limit: Maximum number of chunks to return.
    """
    result = retrieve_debug(query, mode=RETRIEVAL_MODE_KEYWORD, limit=_bounded_limit(limit), offset=0)
    return result["chunks"]


def semantic_search(query: str, limit: int = DEFAULT_CHUNK_LIMIT) -> list[dict]:
    """Search indexed chunks with vector similarity for meaning and paraphrases.

    Args:
        query: Natural-language concept or question to embed when wording is uncertain or semantic
            similarity matters more than exact terms.
        limit: Maximum number of chunks to return.
    """
    result = retrieve_debug(query, mode=RETRIEVAL_MODE_SEMANTIC, limit=_bounded_limit(limit), offset=0)
    return result["chunks"]


def read_document(
    path: str,
    start_line: int = 1,
    max_lines: int = DEFAULT_DOCUMENT_LINES,
    max_chars: int = DEFAULT_DOCUMENT_CHARS,
) -> dict:
    """Read a line window from normalized Markdown for a known source document path.

    Args:
        path: Exact source document path from a previous result, such as
            /documents/Vehicles/Vehicle Purchase Contract.pdf. This is a source path, not a normalized
            Markdown path.
        start_line: One-based line number where reading should begin.
        max_lines: Maximum number of lines to read.
        max_chars: Maximum number of characters to return.
    """
    start_line = max(1, start_line)
    max_lines = max(1, min(max_lines, 1000))
    max_chars = max(1000, min(max_chars, 50000))
    markdown_path = _markdown_path_for_document(path)
    if markdown_path is None:
        return {
            "path": path,
            "markdown_path": None,
            "content": "",
            "start_line": start_line,
            "end_line": start_line - 1,
            "total_lines": 0,
            "truncated": False,
            "error": "No normalized Markdown path found for document.",
        }

    try:
        content = markdown_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {
            "path": path,
            "markdown_path": str(markdown_path),
            "content": "",
            "start_line": start_line,
            "end_line": start_line - 1,
            "total_lines": 0,
            "truncated": False,
            "error": str(exc),
        }

    lines = content.splitlines()
    selected_lines = lines[start_line - 1 : start_line - 1 + max_lines]
    selected_content = "\n".join(selected_lines)
    bounded_content = selected_content[:max_chars]
    end_line = start_line + len(selected_lines) - 1
    return {
        "path": path,
        "markdown_path": str(markdown_path),
        "content": bounded_content,
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": len(lines),
        "truncated": end_line < len(lines) or len(selected_content) > max_chars,
        "error": None,
    }


def grep_documents(
    query: str,
    path: str | None = None,
    path_prefix: str | None = None,
    limit: int = DEFAULT_GREP_LIMIT,
    context_chars: int = DEFAULT_GREP_CONTEXT_CHARS,
) -> list[dict]:
    """Search normalized Markdown with a literal case-insensitive text match.

    Args:
        query: Exact short text to find, such as vendor names, form labels, VINs, dates, or dollar
            amounts.
        path: Optional exact source document path to search. This is a source path, not a normalized
            Markdown path.
        path_prefix: Optional source document directory prefix, such as /documents/Vehicles.
        limit: Maximum number of matches to return.
        context_chars: Number of surrounding characters to include around each match.
    """
    query = query.strip()
    if not query:
        return []

    limit = _bounded_limit(limit)
    context_chars = max(40, min(context_chars, 1000))
    folded_query = query.casefold()
    matches = []

    for document in _normalized_documents(path=path, path_prefix=path_prefix):
        try:
            content = document["markdown_path"].read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        offset = 0
        folded_content = content.casefold()
        while len(matches) < limit:
            match_start = folded_content.find(folded_query, offset)
            if match_start == -1:
                break

            match_end = match_start + len(query)
            context_start = max(0, match_start - context_chars)
            context_end = min(len(content), match_end + context_chars)
            matches.append(
                {
                    "path": document["path"],
                    "filename": document["filename"],
                    "markdown_path": str(document["markdown_path"]),
                    "line": content.count("\n", 0, match_start) + 1,
                    "content": content[context_start:context_end].strip(),
                }
            )
            offset = max(match_end, match_start + 1)

        if len(matches) >= limit:
            break

    return matches


AGENT_TOOL_FUNCTIONS = {
    "search_documents": search_documents,
    "keyword_search": keyword_search,
    "semantic_search": semantic_search,
    "grep_documents": grep_documents,
    "read_document": read_document,
    "remember": remember,
}


def render_tool_descriptions() -> str:
    descriptions = []
    for tool_name in AGENT_TOOL_NAMES:
        tool_function = AGENT_TOOL_FUNCTIONS[tool_name]
        summary, argument_docs = _tool_doc_parts(tool_function)
        descriptions.append(f"- {tool_name}: {summary}")
        descriptions.append("  Arguments:")
        for argument_name in inspect.signature(tool_function).parameters:
            description = argument_docs.get(argument_name, "No description provided.")
            descriptions.append(f"  - {argument_name}: {description}")
    return "\n".join(descriptions)


def _tool_doc_parts(tool_function: Any) -> tuple[str, dict[str, str]]:
    doc = inspect.getdoc(tool_function) or ""
    lines = doc.splitlines()
    summary = lines[0].strip() if lines else "No description provided."
    argument_docs: dict[str, str] = {}
    current_argument: str | None = None
    in_args = False

    for raw_line in lines[1:]:
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped == "Args:":
            in_args = True
            continue
        if not in_args:
            continue

        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", stripped)
        if match:
            current_argument = match.group(1)
            argument_docs[current_argument] = match.group(2).strip()
        elif current_argument:
            argument_docs[current_argument] = f"{argument_docs[current_argument]} {stripped}".strip()

    return summary, argument_docs


def _bounded_limit(limit: Any) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError):
        parsed = DEFAULT_CHUNK_LIMIT
    return max(1, min(parsed, 25))


def _bounded_max_chars(max_chars: Any) -> int:
    try:
        parsed = int(max_chars)
    except (TypeError, ValueError):
        parsed = DEFAULT_DOCUMENT_CHARS
    return max(1000, min(parsed, 50000))


def _bounded_positive_int(value: Any, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, maximum))


def _bounded_context_chars(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_GREP_CONTEXT_CHARS
    return max(40, min(parsed, 1000))


def _normalized_documents(path: str | None = None, path_prefix: str | None = None) -> list[dict]:
    normalized_root = get_settings().common.normalized_output_dir.resolve()
    conditions = [
        "d.missing_since IS NULL",
        "c.metadata ? 'markdown_path'",
    ]
    params: list[Any] = []

    if path:
        conditions.append("d.path = %s")
        params.append(path)
    if path_prefix:
        conditions.append("d.path ILIKE %s")
        params.append(f"{path_prefix.rstrip('/')}%")

    with db_cursor() as (conn, cur):
        cur.execute(
            f"""
            SELECT path, filename, markdown_path
            FROM (
              SELECT DISTINCT ON (d.id)
                d.path,
                d.filename,
                c.metadata->>'markdown_path' AS markdown_path,
                d.modified_time
              FROM documents d
              JOIN chunks c ON c.document_id = d.id
              WHERE {" AND ".join(conditions)}
              ORDER BY d.id, c.chunk_index
            ) normalized_documents
            ORDER BY modified_time DESC
            """,
            params,
        )
        rows = cur.fetchall()
        conn.rollback()

    documents = []
    for source_path, filename, raw_markdown_path in rows:
        markdown_path = Path(raw_markdown_path).resolve()
        try:
            markdown_path.relative_to(normalized_root)
        except ValueError:
            continue
        documents.append(
            {
                "path": source_path,
                "filename": filename,
                "markdown_path": markdown_path,
            }
        )
    return documents


def _markdown_path_for_document(path: str) -> Path | None:
    normalized_root = get_settings().common.normalized_output_dir.resolve()
    with db_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT c.metadata->>'markdown_path'
            FROM documents d
            JOIN chunks c ON c.document_id = d.id
            WHERE d.path = %s AND c.metadata ? 'markdown_path'
            ORDER BY c.chunk_index
            LIMIT 1
            """,
            (path,),
        )
        row = cur.fetchone()
        conn.rollback()

    if not row or not row[0]:
        return None
    markdown_path = Path(row[0]).resolve()
    try:
        markdown_path.relative_to(normalized_root)
    except ValueError:
        return None
    return markdown_path
