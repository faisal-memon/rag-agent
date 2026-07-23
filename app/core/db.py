from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.core.config import DatabaseSettings


def get_connection(database: DatabaseSettings) -> psycopg.Connection:
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
def db_cursor(database: DatabaseSettings):
    conn = get_connection(database)
    try:
        with conn.cursor() as cur:
            yield conn, cur
    finally:
        conn.close()
