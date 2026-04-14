"""Normalize model output into a draft_document structure."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _fallback_from_tool_messages(messages: list[BaseMessage]) -> dict[str, Any]:
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            try:
                payload = msg.content
                if isinstance(payload, dict):
                    raw = payload
                elif isinstance(payload, str):
                    raw = json.loads(payload)
                else:
                    continue
                tables_raw = raw.get("tables")
                if not isinstance(tables_raw, list):
                    continue
                tables_out: list[dict[str, Any]] = []
                for t in tables_raw:
                    if not isinstance(t, dict):
                        continue
                    name = t.get("table_name")
                    if not isinstance(name, str):
                        continue
                    cols_in = t.get("columns") if isinstance(t.get("columns"), list) else []
                    cols_out = []
                    for c in cols_in:
                        if isinstance(c, dict) and isinstance(c.get("name"), str):
                            cols_out.append(
                                {
                                    "name": c["name"],
                                    "description": "(pending) run LLM to describe",
                                }
                            )
                    tables_out.append(
                        {
                            "table_name": name,
                            "description": "(pending) run LLM to describe",
                            "columns": cols_out,
                        }
                    )
                if tables_out:
                    return {"tables": tables_out}
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
    return {"tables": [], "note": "could not parse draft from messages"}


def parse_draft_from_messages(messages: list[BaseMessage]) -> dict[str, Any]:
    """Build draft_document from the last AI JSON or from tool metadata."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str):
                parsed = _extract_json_object(content)
                if parsed and "tables" in parsed:
                    return parsed
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                joined = "".join(parts)
                parsed = _extract_json_object(joined)
                if parsed and "tables" in parsed:
                    return parsed
    return _fallback_from_tool_messages(messages)


def normalize_tool_result_for_draft(tool_payload: dict[str, Any]) -> dict[str, Any]:
    """Build minimal tables[] from mcp_schema_inspect response (for tests)."""
    tables_in = tool_payload.get("tables")
    if not isinstance(tables_in, list):
        return {"tables": []}
    out: list[dict[str, Any]] = []
    for t in tables_in:
        if not isinstance(t, dict):
            continue
        name = t.get("table_name")
        if not isinstance(name, str):
            continue
        cols_in = t.get("columns") if isinstance(t.get("columns"), list) else []
        cols_out = []
        for c in cols_in:
            if isinstance(c, dict) and isinstance(c.get("name"), str):
                cols_out.append({"name": c["name"], "description": "(pending)"})
        out.append(
            {
                "table_name": name,
                "description": "(pending)",
                "columns": cols_out,
            }
        )
    return {"tables": out}
