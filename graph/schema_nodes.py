"""LangGraph nodes for the Schema Agent."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from graph.schema_format import normalize_tool_result_for_draft, parse_draft_from_messages
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
    logger.info(
        "schema_graph_node",
        extra={"node": "agent", "session_id": session_id},
    )
    iteration = int(state.get("iteration") or 0)
    has_existing = bool(state.get("has_existing_schema"))
    # First-time documentation with preloaded metadata avoids a redundant tool roundtrip.
    should_skip_tool_binding = iteration == 0 and not has_existing and isinstance(
        state.get("preloaded_schema_metadata"),
        dict,
    )
    model = get_chat_model()
    if not should_skip_tool_binding:
        model = model.bind_tools([_SCHEMA_TOOL])
    raw_messages = list(state.get("messages") or [])
    messages = _ensure_system_prompt(raw_messages)
    response = model.invoke(messages)
    return {
        "messages": [response],
        "iteration": iteration + 1,
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
    """Normalize assistant output into a JSON-serializable draft_document.

    HITL always receives a **complete** schema document: merge partial model output
    with persisted schema (incremental) and/or DB skeleton (first run / reset).
    """
    session_id = state.get("session_id", "")
    logger.info(
        "schema_graph_node",
        extra={"node": "format_draft", "session_id": session_id},
    )
    messages = list(state.get("messages") or [])
    draft = parse_draft_from_messages(messages)
    if not isinstance(draft, dict):
        draft = {}
    if draft.get("tables") is None:
        draft["tables"] = []

    reset = bool(state.get("reset_schema"))

    # Incremental edits: overlay on last approved snapshot (never show a partial doc in HITL).
    if state.get("has_existing_schema") and not reset:
        prev_entry = SchemaDocsStore().latest()
        prev_doc = prev_entry.get("document") if isinstance(prev_entry, dict) else None
        if isinstance(prev_doc, dict) and prev_doc.get("tables"):
            draft = _merge_schema_documents(prev_doc, draft)

    # First-time or regenerate: ensure every public table appears using preloaded metadata.
    meta = state.get("preloaded_schema_metadata")
    if isinstance(meta, dict) and meta.get("tables"):
        skeleton = normalize_tool_result_for_draft(meta)
        if isinstance(skeleton.get("tables"), list) and skeleton["tables"]:
            draft = _merge_schema_documents(skeleton, draft)

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
    previous = store.latest()
    if previous:
        prev_doc = previous.get("document")
        if isinstance(prev_doc, dict):
            doc = _merge_schema_documents(prev_doc, doc)
    entry = store.save_approved(
        session_id=str(session_id or "unknown"),
        document=doc,
    )
    return {"approved_document": entry, "status": "persisted", "error": None}


def _merge_schema_documents(base_doc: dict[str, Any], patch_doc: dict[str, Any]) -> dict[str, Any]:
    """Merge partial table updates into existing schema document.

    Never drops tables from base unless patch explicitly lists fewer tables *and*
    that is interpreted as replace-all (we avoid that: partial updates merge by name).
    """
    base_tables = base_doc.get("tables")
    patch_tables = patch_doc.get("tables")

    if not isinstance(base_tables, list) or not base_tables:
        return patch_doc if isinstance(patch_doc, dict) else base_doc

    # Missing or invalid patch tables: keep base entirely (do not wipe on bad LLM output).
    if patch_tables is None or not isinstance(patch_tables, list):
        return dict(base_doc)

    # Empty patch list = no-op on tables (preserve all).
    if len(patch_tables) == 0:
        out = dict(base_doc)
        for key, val in patch_doc.items():
            if key != "tables":
                out[key] = val
        return out

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for table in base_tables:
        if not isinstance(table, dict):
            continue
        name = table.get("table_name")
        if not isinstance(name, str):
            continue
        merged[name] = dict(table)
        order.append(name)

    for table in patch_tables:
        if not isinstance(table, dict):
            continue
        name = table.get("table_name")
        if not isinstance(name, str):
            continue
        if name in merged:
            merged[name] = _merge_table_entry(merged[name], table)
        else:
            order.append(name)
            merged[name] = dict(table)

    out_tables = [merged[name] for name in order if name in merged]
    out_doc = dict(base_doc)
    for key, val in patch_doc.items():
        if key == "tables":
            continue
        out_doc[key] = val
    out_doc["tables"] = out_tables
    return out_doc


def _merge_table_entry(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge one table: patch overrides description; columns merge by name."""
    out = dict(base)
    for key, val in patch.items():
        if key == "columns":
            continue
        out[key] = val
    base_cols = base.get("columns")
    patch_cols = patch.get("columns")
    if isinstance(patch_cols, list) and patch_cols:
        if not isinstance(base_cols, list):
            out["columns"] = list(patch_cols)
        else:
            by_name: dict[str, dict[str, Any]] = {}
            order_names: list[str] = []
            for c in base_cols:
                if isinstance(c, dict) and isinstance(c.get("name"), str):
                    n = c["name"]
                    by_name[n] = dict(c)
                    order_names.append(n)
            for c in patch_cols:
                if not isinstance(c, dict) or not isinstance(c.get("name"), str):
                    continue
                n = c["name"]
                if n in by_name:
                    merged_c = dict(by_name[n])
                    merged_c.update(c)
                    by_name[n] = merged_c
                else:
                    by_name[n] = dict(c)
                    order_names.append(n)
            out["columns"] = [by_name[n] for n in order_names if n in by_name]
    elif isinstance(base_cols, list):
        out["columns"] = base_cols
    return out


def create_initial_messages(
    user_message: str,
    *,
    preloaded_schema_metadata: dict[str, Any] | None = None,
) -> list[BaseMessage]:
    messages: list[BaseMessage] = [HumanMessage(content=user_message)]
    if isinstance(preloaded_schema_metadata, dict):
        messages.append(
            HumanMessage(
                content=(
                    "Preloaded schema metadata (use this directly if possible):\n"
                    f"{json.dumps(preloaded_schema_metadata, ensure_ascii=False)}"
                )
            )
        )
    return messages
