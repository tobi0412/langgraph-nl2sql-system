"""Schema Agent graph: interrupt + resume with mocked LLM (no network)."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from agents.schema_agent import SchemaAgentRunner


def test_schema_flow_interrupt_then_approve_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings,
        "schema_docs_path",
        str(tmp_path / "schema_docs.json"),
    )

    class _FakeChat:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(
                content=json.dumps(
                    {
                        "tables": [
                            {
                                "table_name": "film",
                                "description": "Films catalog",
                                "columns": [{"name": "film_id", "description": "PK"}],
                            }
                        ]
                    }
                )
            )

    import graph.schema_nodes as sn

    monkeypatch.setattr(sn, "get_chat_model", lambda *a, **k: _FakeChat())

    runner = SchemaAgentRunner()
    out1 = runner.start(user_id="u1", session_id="thread-1")
    assert "__interrupt__" in out1 or "human_feedback" not in out1

    out2 = runner.resume(session_id="thread-1", human_feedback={"action": "approve"})
    assert out2.get("status") == "persisted"
    assert out2.get("approved_document", {}).get("document", {}).get("tables")

    from memory.schema_docs_store import SchemaDocsStore

    store = SchemaDocsStore(path=str(tmp_path / "schema_docs.json"))
    entries = store.list_approved(user_id="u1")
    assert len(entries) >= 1
