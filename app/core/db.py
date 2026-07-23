from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.config import get_settings


def get_connection() -> psycopg.Connection:
    database = get_settings().common.database
    conn = psycopg.connect(
        dbname=database.db,
        user=database.user,
        password=database.password,
        host=database.host,
        port=database.port,
        autocommit=False,
    )
    register_vector(conn)
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield conn, cur
    finally:
        conn.close()
