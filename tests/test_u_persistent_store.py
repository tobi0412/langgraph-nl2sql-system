"""Unit tests for PersistentStore (v2 JSON-first model)."""

from __future__ import annotations

import json

import pytest

from memory.persistent_store import (
    DEFAULT_PREFERENCES,
    MAX_INSTRUCTIONS,
    PersistentStore,
)
from settings import settings


@pytest.fixture
def prefs_path(tmp_path, monkeypatch):
    path = tmp_path / "user_preferences.json"
    monkeypatch.setattr(settings, "user_preferences_path", str(path))
    monkeypatch.setattr(settings, "preferences_store_backend", "json")
    return path


def test_u_persistent_preferences_survive_new_instance(prefs_path):
    p1 = PersistentStore()
    merged = p1.update_preferences(
        "alice",
        language="en",
        add_instructions=["Use markdown tables for tabular results."],
    )
    assert merged["language"] == "en"
    assert "Use markdown tables for tabular results." in merged["instructions"]

    p2 = PersistentStore()
    got = p2.get_preferences("alice")
    assert got["language"] == "en"
    assert "Use markdown tables for tabular results." in got["instructions"]
    raw = json.loads(prefs_path.read_text(encoding="utf-8"))
    assert "alice" in raw["users"]
    assert raw["version"] == 2


def test_u_persistent_default_preferences(prefs_path):
    store = PersistentStore()
    got = store.get_preferences("unknown")
    assert got == DEFAULT_PREFERENCES


def test_u_persistent_preferences_cap_fifo_eviction(prefs_path):
    """When the LLM dumps more instructions than the cap allows and
    forgets to ``remove_instructions``, the store falls back to FIFO
    eviction so the newest instructions always survive.
    """
    store = PersistentStore()
    too_many = [f"Instruction number {i}." for i in range(MAX_INSTRUCTIONS + 5)]
    merged = store.update_preferences("alice", add_instructions=too_many)
    assert len(merged["instructions"]) == MAX_INSTRUCTIONS
    # First 5 dropped, last MAX_INSTRUCTIONS kept
    assert merged["instructions"][0] == "Instruction number 5."
    assert merged["instructions"][-1] == f"Instruction number {MAX_INSTRUCTIONS + 4}."


def test_u_persistent_preferences_explicit_replace(prefs_path):
    """The LLM can target the exact text of an existing instruction to
    replace it — this is how the 20-preference cap is respected when
    adding a new directive at capacity.
    """
    store = PersistentStore()
    initial = [f"Instruction {i}." for i in range(MAX_INSTRUCTIONS)]
    store.update_preferences("alice", add_instructions=initial)

    merged = store.update_preferences(
        "alice",
        add_instructions=["Use ISO date format (YYYY-MM-DD)."],
        remove_instructions=["Instruction 0."],
    )
    assert "Instruction 0." not in merged["instructions"]
    assert "Use ISO date format (YYYY-MM-DD)." in merged["instructions"]
    assert len(merged["instructions"]) == MAX_INSTRUCTIONS


def test_u_persistent_preferences_dedupe_case_insensitive(prefs_path):
    store = PersistentStore()
    store.update_preferences("alice", add_instructions=["Prefer ISO dates."])
    merged = store.update_preferences("alice", add_instructions=["prefer iso dates."])
    assert merged["instructions"].count("Prefer ISO dates.") == 1
    assert "prefer iso dates." not in merged["instructions"]
