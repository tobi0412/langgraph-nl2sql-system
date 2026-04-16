"""Unit tests for PersistentStore (iteration 5)."""

from __future__ import annotations

import json

import pytest

from memory.persistent_store import DEFAULT_PREFERENCES, PersistentStore
from settings import settings


@pytest.fixture
def prefs_path(tmp_path, monkeypatch):
    path = tmp_path / "user_preferences.json"
    monkeypatch.setattr(settings, "user_preferences_path", str(path))
    monkeypatch.setattr(settings, "preferences_store_backend", "json")
    return path


def test_u_persistent_preferences_survive_new_instance(prefs_path):
    p1 = PersistentStore()
    merged = p1.merge_preferences("alice", {"language": "en", "strictness": "high"})
    assert merged["language"] == "en"
    assert merged["strictness"] == "high"

    p2 = PersistentStore()
    got = p2.get_preferences("alice")
    assert got["language"] == "en"
    assert got["strictness"] == "high"
    raw = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert "alice" in raw["users"]


def test_u_persistent_default_preferences(prefs_path):
    store = PersistentStore()
    got = store.get_preferences("unknown")
    assert got == DEFAULT_PREFERENCES
