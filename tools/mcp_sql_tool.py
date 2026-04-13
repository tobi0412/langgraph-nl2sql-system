"""Herramienta MCP para SQL en modo solo lectura (base)."""

import psycopg

from settings import settings


def execute_readonly_sql(sql: str) -> list[tuple]:
    """Ejecuta SQL de solo lectura.

    Proteccion inicial: solo permite sentencias SELECT.
    """
    normalized = sql.strip().lower()
    if not normalized.startswith("select"):
        raise ValueError("Solo se permite SQL de lectura (SELECT).")

    with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()
