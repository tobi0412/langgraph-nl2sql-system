"""Typed state for the Query Agent LangGraph."""

from __future__ import annotations

from typing import Any, TypedDict


class QueryAgentState(TypedDict, total=False):
    """State for query flow with planner, critic and execution."""

    session_id: str
    user_id: str
    question: str
    schema_context: dict[str, list[str]]

    persistent_prefs: dict[str, str]
    memory_context_text: str

    intent: str
    candidate_tables: list[str]
    candidate_columns: list[str]
    needs_clarification: bool
    clarification_question: str | None

    sql_candidate: str | None
    validator: dict[str, Any]

    status: str
    explanation: str
    limitations: list[str]
    sample: dict[str, Any] | None
