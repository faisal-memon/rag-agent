"""Postgres-backed conversation history for authenticated agent sessions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.agent.config import get_api_settings
from app.core.db import db_cursor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY,
  user_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
  ON conversations (user_name, updated_at DESC);
CREATE TABLE IF NOT EXISTS conversation_messages (
  id BIGSERIAL PRIMARY KEY,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_order
  ON conversation_messages (conversation_id, id);
CREATE TABLE IF NOT EXISTS agent_requests (
  request_id TEXT PRIMARY KEY,
  user_name TEXT NOT NULL,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('running', 'complete', 'error', 'interrupted')),
  result JSONB,
  error JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours'
);
CREATE INDEX IF NOT EXISTS idx_agent_requests_expiration ON agent_requests (expires_at);
"""


def _ensure_schema(cur) -> None:
    cur.execute(_SCHEMA)


def load_or_create(user_name: str, conversation_id: str | None) -> tuple[str, list[dict]]:
    requested = UUID(conversation_id) if conversation_id else uuid4()
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute(
            """INSERT INTO conversations (id, user_name) VALUES (%s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (requested, user_name),
        )
        cur.execute(
            """SELECT c.user_name, m.role, m.content
               FROM conversations c
               LEFT JOIN conversation_messages m ON m.conversation_id = c.id
               WHERE c.id = %s
               ORDER BY m.id DESC LIMIT 20""",
            (requested,),
        )
        rows = cur.fetchall()
        if rows and rows[0][0] != user_name:
            raise PermissionError("Conversation does not belong to this user")
        history = list(reversed([(role, content) for _, role, content in rows if role is not None]))
        conn.commit()
    return str(requested), [{"role": role, "content": content} for role, content in history]


def append_messages(user_name: str, conversation_id: str, messages: list[dict]) -> None:
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute("SELECT user_name FROM conversations WHERE id = %s", (UUID(conversation_id),))
        owner = cur.fetchone()
        if owner is None or owner[0] != user_name:
            raise PermissionError("Conversation does not belong to this user")
        cur.executemany(
            "INSERT INTO conversation_messages (conversation_id, role, content) VALUES (%s, %s, %s)",
            [(UUID(conversation_id), item["role"], item["content"]) for item in messages],
        )
        cur.execute("UPDATE conversations SET updated_at = NOW() WHERE id = %s", (UUID(conversation_id),))
        conn.commit()


def create_agent_request(user_name: str, conversation_id: str, request_id: str) -> None:
    """Persist a running request so its result can be recovered after a stream drops."""
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute("DELETE FROM agent_requests WHERE expires_at <= NOW()")
        cur.execute(
            """INSERT INTO agent_requests (request_id, user_name, conversation_id, status)
               VALUES (%s, %s, %s, 'running')""",
            (request_id, user_name, UUID(conversation_id)),
        )
        conn.commit()


def complete_agent_request(user_name: str, request_id: str, result: dict) -> None:
    """Store the completed response for the authenticated request owner."""
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute(
            """UPDATE agent_requests SET status = 'complete', result = %s, error = NULL
               WHERE request_id = %s AND user_name = %s AND status = 'running'""",
            (Jsonb(result), request_id, user_name),
        )
        conn.commit()


def fail_agent_request(user_name: str, request_id: str, error: dict) -> None:
    """Store a bounded error response for later retrieval."""
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute(
            """UPDATE agent_requests SET status = 'error', error = %s
               WHERE request_id = %s AND user_name = %s AND status = 'running'""",
            (Jsonb(error), request_id, user_name),
        )
        conn.commit()


def get_agent_request(user_name: str, request_id: str) -> dict | None:
    """Return a non-expired request only to the user who started it."""
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute(
            """UPDATE agent_requests SET status = 'interrupted'
               WHERE request_id = %s AND user_name = %s AND status = 'running'
                 AND created_at <= NOW() - INTERVAL '15 minutes'
                 AND expires_at > NOW()""",
            (request_id, user_name),
        )
        cur.execute(
            """SELECT status, result, error FROM agent_requests
               WHERE request_id = %s AND user_name = %s AND expires_at > NOW()""",
            (request_id, user_name),
        )
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return None
    return {"status": row[0], "result": row[1], "error": row[2]}

def list_conversations(user_name: str, limit: int = 50) -> list[dict]:
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute("""SELECT c.id, c.created_at, c.updated_at,
                              (SELECT content FROM conversation_messages m
                               WHERE m.conversation_id = c.id AND m.role = 'user'
                               ORDER BY m.id LIMIT 1)
                       FROM conversations c WHERE c.user_name = %s
                       ORDER BY c.updated_at DESC LIMIT %s""", (user_name, limit))
        return [{"id": str(row[0]), "created_at": row[1].isoformat(), "updated_at": row[2].isoformat(), "title": row[3] or "New conversation"} for row in cur.fetchall()]

def get_conversation(user_name: str, conversation_id: str) -> list[dict] | None:
    """Return one user's conversation, or None when it does not exist for that user."""
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute(
            """SELECT m.role, m.content
               FROM conversations c
               LEFT JOIN conversation_messages m ON m.conversation_id = c.id
               WHERE c.id = %s AND c.user_name = %s
               ORDER BY m.id DESC LIMIT 20""",
            (UUID(conversation_id), user_name),
        )
        rows = list(reversed(cur.fetchall()))
        if not rows:
            cur.execute("SELECT 1 FROM conversations WHERE id = %s AND user_name = %s", (UUID(conversation_id), user_name))
            if cur.fetchone() is None:
                return None
        return [{"role": role, "content": content} for role, content in rows if role is not None]

def delete_conversation(user_name: str, conversation_id: str) -> bool:
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute("DELETE FROM conversations WHERE id = %s AND user_name = %s", (UUID(conversation_id), user_name))
        conn.commit()
        return cur.rowcount > 0
