"""Unit tests for SQL guardrails."""

import pytest

from tools.sql_guard import validate_read_only_sql


def test_u_sql_guard_accepts_select():
    normalized = validate_read_only_sql("SELECT * FROM film LIMIT 5;")
    assert normalized.startswith("select")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM film;",
        "UPDATE film SET title = 'X';",
        "INSERT INTO film(title) VALUES ('X');",
        "DROP TABLE film;",
        "ALTER TABLE film ADD COLUMN x INT;",
        "TRUNCATE TABLE film;",
        "CREATE TABLE t(id INT);",
    ],
)
def test_u_sql_guard_rejects_non_allowed(sql: str):
    with pytest.raises(ValueError):
        validate_read_only_sql(sql)
