"""Integration tests de conectividad y tablas clave en PostgreSQL."""

import psycopg
import pytest

from db import check_database_connection, get_existing_tables


def _require_db() -> None:
    try:
        check_database_connection()
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB no disponible para integration tests: {exc}")


def test_in_db_connection_success():
    _require_db()
    assert check_database_connection() is True


def test_in_db_has_core_dvdrental_tables():
    _require_db()
    existing = get_existing_tables(["film", "actor", "rental"])
    assert {"film", "actor", "rental"}.issubset(existing)
