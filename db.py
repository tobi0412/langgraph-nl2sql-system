"""Utilidades de conexion y chequeos de PostgreSQL."""

from collections.abc import Iterable

import psycopg

from settings import settings


def check_database_connection() -> bool:
    """Retorna True si puede ejecutar SELECT 1."""
    with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
    return True


def get_existing_tables(table_names: Iterable[str]) -> set[str]:
    """Devuelve el subconjunto de tablas existentes en schema public."""
    tables = list(table_names)
    if not tables:
        return set()

    query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ANY(%s);
    """
    with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (tables,))
            rows = cur.fetchall()
    return {row[0] for row in rows}
