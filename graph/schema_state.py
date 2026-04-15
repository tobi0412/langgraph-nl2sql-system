"""Typed state for the Schema Agent LangGraph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SchemaAgentState(TypedDict, total=False):
    """State for schema documentation flow with HITL."""

    messages: Annotated[list[AnyMessage], add_messages]
    iteration: int
    max_iterations: int
    session_id: str
    draft_document: dict[str, Any]
    human_feedback: dict[str, Any]
    status: str
    approved_document: dict[str, Any] | None
    error: str | None
    has_existing_schema: bool
    reset_schema: bool
    preloaded_schema_metadata: dict[str, Any] | None
