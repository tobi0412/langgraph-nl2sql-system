"""Routing for the Schema Agent graph."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage

from graph.schema_state import SchemaAgentState


def route_after_schema_agent(
    state: SchemaAgentState,
) -> Literal["tools", "format_draft"]:
    """Send tool calls to the tools node; otherwise normalize draft."""
    messages = state.get("messages") or []
    if not messages:
        return "format_draft"
    last = messages[-1]
    if not isinstance(last, AIMessage):
        return "format_draft"
    tool_calls = getattr(last, "tool_calls", None) or []
    max_it = int(state.get("max_iterations") or 10)
    iteration = int(state.get("iteration") or 0)
    if tool_calls and iteration < max_it:
        return "tools"
    return "format_draft"
