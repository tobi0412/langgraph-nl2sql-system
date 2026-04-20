"""Routing for Query Agent graph."""

from __future__ import annotations

from typing import Literal

from graph.query_state import QueryAgentState


def route_after_prefs_update(state: QueryAgentState) -> Literal["prepare", "finish"]:
    """Short-circuit to finish when the turn was only a preferences directive."""
    if state.get("status") == "preferences_updated":
        return "finish"
    return "prepare"


def route_after_prepare(state: QueryAgentState) -> Literal["finish", "planner"]:
    """If schema is missing, finish early; otherwise continue."""
    if state.get("status") == "blocked_missing_schema":
        return "finish"
    return "planner"


def route_after_planner(state: QueryAgentState) -> Literal["finish", "critic"]:
    """If no candidate tables or clarification needed, finish early."""
    if state.get("needs_clarification") or not (state.get("candidate_tables") or []):
        return "finish"
    return "critic"


def route_after_critic(state: QueryAgentState) -> Literal["finish", "execute", "planner"]:
    """Execute SQL when approved; send back to planner on repairable issues.

    The critic node sets ``plan_feedback`` (with source="critic") when the SQL
    was rejected on a fixable correctness/syntax ground and retry budget
    remains. In that case we loop back to planner; otherwise we finish
    (either to ask the user, or because we already exhausted retries).
    """
    validator = state.get("validator") or {}
    if validator.get("approved") and not validator.get("needs_clarification"):
        return "execute"
    if (
        state.get("plan_feedback")
        and state.get("plan_feedback_source") == "critic"
    ):
        return "planner"
    return "finish"


def route_after_execute(state: QueryAgentState) -> Literal["finish", "planner"]:
    """Retry the planner when a runtime SQL error fit the retry budget."""
    if (
        state.get("plan_feedback")
        and state.get("plan_feedback_source") == "execution"
    ):
        return "planner"
    return "finish"
