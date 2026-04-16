"""Integration-style tests for memory + Query Agent (iteration 5)."""

from __future__ import annotations

import graph.query_nodes as query_nodes_module
import pytest

from graph.query_nodes import _build_memory_context_text
from memory.persistent_store import PersistentStore
from memory.session_store import SessionSnapshot, SessionStore
from settings import settings


@pytest.fixture
def memory_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "user_preferences_path", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(settings, "preferences_store_backend", "json")
    monkeypatch.setattr(settings, "session_memory_path", str(tmp_path / "sess.json"))
    monkeypatch.setattr(settings, "session_memory_ttl_seconds", 3600)


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch):
    monkeypatch.setattr(query_nodes_module.settings, "llm_api_key", "")


def test_in_followup_planner_sees_prior_sql(memory_paths):
    prefs = PersistentStore().get_preferences("u1")
    snap = SessionSnapshot(
        last_question="Lista films",
        last_sql="SELECT * FROM film LIMIT 5",
        recent_filters=["rental_duration = 3"],
    )
    block = _build_memory_context_text(prefs, snap)
    state = {
        "question": "ordenar por titulo",
        "memory_context_text": block,
        "schema_context": {"film": ["film_id", "title"], "actor": ["actor_id"]},
    }
    out = query_nodes_module.planner_node(state)
    assert out["candidate_tables"] and out["candidate_tables"][0] == "film"


def test_in_new_session_keeps_persistent_prefs(memory_paths):
    PersistentStore().merge_preferences("bob", {"language": "en", "format": "plain"})
    p = PersistentStore().get_preferences("bob")
    empty_snap = SessionSnapshot()
    t1 = _build_memory_context_text(p, empty_snap)
    t2 = _build_memory_context_text(p, empty_snap)
    assert "idioma=en" in t1.replace(" ", "")
    assert t1 == t2

    SessionStore().record_turn(
        "only-sess-a",
        question="x",
        sql_candidate="SELECT 1",
        status="ok",
        clarification_question=None,
        candidate_tables=["film"],
        intent="list",
    )
    assert SessionStore().get_snapshot("only-sess-a").last_sql
