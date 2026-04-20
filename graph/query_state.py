"""Typed state for the Query Agent LangGraph."""

from __future__ import annotations

from typing import Any, TypedDict


class QueryAgentState(TypedDict, total=False):
    """State for query flow with planner, critic and execution."""

    session_id: str
    user_id: str
    question: str
    schema_context: dict[str, dict[str, Any]]

    persistent_prefs: dict[str, str]
    memory_context_text: str
    response_style_instruction: str | None
    pending_clarification_text: str

    intent: str
    candidate_tables: list[str]
    candidate_columns: list[str]
    needs_clarification: bool
    clarification_question: str | None

    sql_candidate: str | None
    validator: dict[str, Any]

    status: str
    assistant_text: str
    explanation: str
    limitations: list[str]
    sample: dict[str, Any] | None

    plan_retry_count: int
    plan_feedback: str | None
    plan_feedback_source: str | None
