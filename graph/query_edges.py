"""Routing for Query Agent graph."""

from __future__ import annotations

from typing import Literal

from graph.query_state import QueryAgentState


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


def route_after_critic(state: QueryAgentState) -> Literal["finish", "execute"]:
    """Only execute SQL when validator approves."""
    validator = state.get("validator") or {}
    if validator.get("approved") and not validator.get("needs_clarification"):
        return "execute"
    return "finish"
