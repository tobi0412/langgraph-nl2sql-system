"""Unit tests: draft parsing."""

import json

from langchain_core.messages import AIMessage, ToolMessage

from graph.schema_format import (
    normalize_tool_result_for_draft,
    parse_draft_from_messages,
)


def test_parse_draft_from_ai_json_string():
    doc = {
        "tables": [
            {
                "table_name": "film",
                "description": "Movies",
                "columns": [{"name": "title", "description": "Title"}],
            }
        ]
    }
    msg = AIMessage(content=json.dumps(doc))
    out = parse_draft_from_messages([msg])
    assert out["tables"][0]["table_name"] == "film"


def test_normalize_tool_payload():
    payload = {
        "tables": [
            {
                "table_name": "actor",
                "columns": [{"name": "actor_id", "data_type": "integer"}],
            }
        ]
    }
    out = normalize_tool_result_for_draft(payload)
    assert out["tables"][0]["table_name"] == "actor"
    assert out["tables"][0]["columns"][0]["name"] == "actor_id"


def test_fallback_from_tool_message_string():
    payload = {"tables": [{"table_name": "rental", "columns": [{"name": "rental_id"}]}]}
    tm = ToolMessage(content=json.dumps(payload), tool_call_id="x")
    out = parse_draft_from_messages([tm])
    assert any(t.get("table_name") == "rental" for t in out.get("tables", []))
