"""Unit tests for SessionStore (iteration 5)."""

from __future__ import annotations

import time

import pytest

from memory.session_store import SessionStore, extract_filters_from_sql
from settings import settings


@pytest.fixture
def session_path(tmp_path, monkeypatch):
    path = tmp_path / "session_memory.json"
    monkeypatch.setattr(settings, "session_memory_path", str(path))
    return path


def test_u_session_isolation_by_session_id(session_path):
    a = SessionStore()
    b = SessionStore()
    a.record_turn(
        "s-a",
        question="q1",
        sql_candidate="SELECT 1",
        status="ok",
        clarification_question=None,
        candidate_tables=["film"],
        intent="list",
    )
    b.record_turn(
        "s-b",
        question="q2",
        sql_candidate="SELECT 2",
        status="ok",
        clarification_question=None,
        candidate_tables=["actor"],
        intent="list",
    )
    assert a.get_snapshot("s-a").last_sql.strip().endswith("1")
    assert b.get_snapshot("s-b").last_question == "q2"
    assert a.get_snapshot("s-b").last_question == "q2"
    assert a.get_snapshot("no-existe").last_question == ""


def test_u_session_ttl_expires(session_path, monkeypatch):
    monkeypatch.setattr(settings, "session_memory_ttl_seconds", 1)
    store = SessionStore()
    store.record_turn(
        "exp",
        question="old",
        sql_candidate=None,
        status="needs_clarification",
        clarification_question="?",
        candidate_tables=[],
        intent=None,
    )
    assert store.get_snapshot("exp").last_question == "old"
    time.sleep(1.2)
    assert store.get_snapshot("exp").last_question == ""


def test_u_extract_filters_from_where():
    sql = "SELECT * FROM film WHERE rental_duration = 3 AND title LIKE 'A%' ORDER BY title;"
    fs = extract_filters_from_sql(sql)
    assert any("rental_duration" in f for f in fs)
    assert any("title" in f for f in fs)
