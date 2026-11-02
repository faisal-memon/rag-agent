"""Postgres storage for ESPHome device identity and shared voice history."""

from __future__ import annotations

from app.agent.config import get_api_settings
from app.core.db import db_cursor

VOICE_DEVICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_devices (
  device_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  host TEXT NOT NULL,
  port INTEGER NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_interaction_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS voice_device_messages (
  id BIGSERIAL PRIMARY KEY,
  device_id TEXT NOT NULL REFERENCES voice_devices(device_id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_voice_device_messages_order
  ON voice_device_messages (device_id, id);
"""


def _ensure_schema(cur) -> None:
    # psycopg's extended protocol does not accept multiple parameterless
    # statements in one execute call. Keep schema setup idempotent and issue
    # each DDL statement separately.
    for statement in VOICE_DEVICE_SCHEMA.split(";"):
        if statement.strip():
            cur.execute(statement)


def register_device(device_id: str, display_name: str, host: str, port: int) -> None:
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute(
            """INSERT INTO voice_devices (device_id, display_name, host, port)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (device_id) DO UPDATE SET
                 display_name = EXCLUDED.display_name,
                 host = EXCLUDED.host,
                 port = EXCLUDED.port,
                 last_connected_at = NOW()""",
            (device_id, display_name, host, port),
        )
        conn.commit()


def list_devices() -> list[dict]:
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute(
            """SELECT device_id, display_name, host, port, first_seen_at,
                      last_connected_at, last_interaction_at
               FROM voice_devices
               ORDER BY COALESCE(last_interaction_at, last_connected_at) DESC"""
        )
        devices = [
            {
                "device_id": row[0],
                "display_name": row[1],
                "host": row[2],
                "port": row[3],
                "first_seen_at": row[4],
                "last_connected_at": row[5],
                "last_interaction_at": row[6],
            }
            for row in cur.fetchall()
        ]
        conn.commit()
        return devices


def load_history(device_id: str, limit: int = 12) -> list[dict[str, str]]:
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute(
            """SELECT role, content FROM voice_device_messages
               WHERE device_id = %s ORDER BY id DESC LIMIT %s""",
            (device_id, limit),
        )
        history = [
            {"role": row[0], "content": row[1]}
            for row in reversed(cur.fetchall())
        ]
        conn.commit()
        return history


def save_turn(
    device_id: str,
    user_message: str,
    assistant_message: str,
    history_limit: int,
) -> None:
    """Persist a complete turn and keep only the context window in storage."""
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.executemany(
            "INSERT INTO voice_device_messages (device_id, role, content) VALUES (%s, %s, %s)",
            [
                (device_id, "user", user_message),
                (device_id, "assistant", assistant_message),
            ],
        )
        cur.execute(
            "UPDATE voice_devices SET last_interaction_at = NOW() WHERE device_id = %s",
            (device_id,),
        )
        cur.execute(
            """DELETE FROM voice_device_messages
               WHERE device_id = %s AND id NOT IN (
                   SELECT id FROM voice_device_messages
                   WHERE device_id = %s ORDER BY id DESC LIMIT %s
               )""",
            (device_id, device_id, history_limit),
        )
        conn.commit()


def get_history(device_id: str, limit: int = 100) -> list[dict] | None:
    with db_cursor(get_api_settings().database) as (conn, cur):
        _ensure_schema(cur)
        cur.execute("SELECT 1 FROM voice_devices WHERE device_id = %s", (device_id,))
        if cur.fetchone() is None:
            conn.commit()
            return None
        cur.execute(
            """SELECT role, content, created_at FROM voice_device_messages
               WHERE device_id = %s ORDER BY id DESC LIMIT %s""",
            (device_id, limit),
        )
        history = [
            {"role": row[0], "content": row[1], "created_at": row[2]}
            for row in reversed(cur.fetchall())
        ]
        conn.commit()
        return history
