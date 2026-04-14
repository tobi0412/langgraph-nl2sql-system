"""LangGraph nodes for the Schema Agent."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from graph.schema_format import parse_draft_from_messages
from graph.schema_state import SchemaAgentState
from llm.chat_model import get_chat_model
from memory.schema_docs_store import SchemaDocsStore
from prompts.schema_agent import SCHEMA_SYSTEM_PROMPT
from tools.mcp_schema_tool import MCPSchemaInspectTool

logger = logging.getLogger(__name__)

_SCHEMA_TOOL = MCPSchemaInspectTool()
_TOOLS_NODE = ToolNode([_SCHEMA_TOOL])


def _ensure_system_prompt(messages: list[BaseMessage]) -> list[BaseMessage]:
    if messages and isinstance(messages[0], SystemMessage):
        return messages
    return [SystemMessage(content=SCHEMA_SYSTEM_PROMPT), *messages]


def schema_agent_node(state: SchemaAgentState) -> dict[str, Any]:
    """LLM with `mcp_schema_inspect` bound (ReAct-style loop with ToolNode)."""
    session_id = state.get("session_id", "")
    user_id = state.get("user_id", "")
    logger.info(
        "schema_graph_node",
        extra={"node": "agent", "session_id": session_id, "user_id": user_id},
    )
    model = get_chat_model().bind_tools([_SCHEMA_TOOL])
    raw_messages = list(state.get("messages") or [])
    messages = _ensure_system_prompt(raw_messages)
    response = model.invoke(messages)
    return {
        "messages": [response],
        "iteration": int(state.get("iteration") or 0) + 1,
    }


def schema_tools_node(state: SchemaAgentState) -> dict[str, Any]:
    """Execute tool calls emitted by the agent."""
    session_id = state.get("session_id", "")
    logger.info(
        "schema_graph_node",
        extra={"node": "tools", "session_id": session_id},
    )
    return _TOOLS_NODE.invoke(state)


def format_draft_node(state: SchemaAgentState) -> dict[str, Any]:
    """Normalize assistant output into a JSON-serializable draft_document."""
    session_id = state.get("session_id", "")
    logger.info(
        "schema_graph_node",
        extra={"node": "format_draft", "session_id": session_id},
    )
    messages = list(state.get("messages") or [])
    draft = parse_draft_from_messages(messages)
    return {"draft_document": draft, "status": "draft_ready"}


def human_gate_node(state: SchemaAgentState) -> dict[str, Any]:
    """HITL checkpoint: interrupt until human resumes with feedback dict."""
    session_id = state.get("session_id", "")
    if state.get("human_feedback"):
        logger.info(
            "schema_graph_node",
            extra={"node": "human_gate", "session_id": session_id, "skipped": True},
        )
        return {}
    logger.info(
        "schema_graph_node",
        extra={"node": "human_gate", "session_id": session_id, "interrupt": True},
    )
    payload = interrupt(
        {
            "status": "awaiting_human",
            "draft_document": state.get("draft_document"),
            "session_id": session_id,
            "user_id": state.get("user_id", ""),
        }
    )
    return {"human_feedback": payload}


def persist_approved_node(state: SchemaAgentState) -> dict[str, Any]:
    """Persist approved or edited document; reject skips storage."""
    session_id = state.get("session_id", "")
    logger.info(
        "schema_graph_node",
        extra={"node": "persist_approved", "session_id": session_id},
    )
    fb = state.get("human_feedback") or {}
    action = fb.get("action")
    if action == "reject":
        return {
            "status": "rejected",
            "approved_document": None,
            "error": str(fb.get("reason", "rejected")),
        }
    if action == "approve":
        doc = state.get("draft_document")
    elif action == "edit":
        doc = fb.get("edited_document", state.get("draft_document"))
    else:
        return {
            "status": "error",
            "error": "human_feedback must include action approve|edit|reject",
            "approved_document": None,
        }
    if not isinstance(doc, dict):
        return {"status": "error", "error": "no document to persist", "approved_document": None}

    store = SchemaDocsStore()
    entry = store.save_approved(
        user_id=str(state.get("user_id") or "anonymous"),
        session_id=str(session_id or "unknown"),
        document=doc,
    )
    return {"approved_document": entry, "status": "persisted", "error": None}


def create_initial_messages(user_message: str) -> list[BaseMessage]:
    return [HumanMessage(content=user_message)]
