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

    class _InspectStub:
        def _run(self):
            return {
                "tool": "mcp_schema_inspect",
                "table_count": 1,
                "tables": [
                    {
                        "table_name": "film",
                        "columns": [{"name": "film_id", "data_type": "integer"}],
                        "primary_keys": ["film_id"],
                        "foreign_keys": [],
                        "constraints": [],
                    }
                ],
            }

    runner = SchemaAgentRunner(schema_inspect_tool=_InspectStub())
    out1 = runner.start(session_id="thread-1")
    assert "__interrupt__" in out1 or "human_feedback" not in out1

    out2 = runner.resume(session_id="thread-1", human_feedback={"action": "approve"})
    assert out2.get("status") == "persisted"
    assert out2.get("approved_document", {}).get("document", {}).get("tables")

    from memory.schema_docs_store import SchemaDocsStore

    store = SchemaDocsStore(path=str(tmp_path / "schema_docs.json"))
    entries = store.list_approved()
    assert len(entries) >= 1


def test_schema_start_skips_preload_when_user_has_existing_schema(monkeypatch: pytest.MonkeyPatch):
    import graph.schema_nodes as sn

    class _FakeChat:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            return AIMessage(content='{"tables":[]}')

    monkeypatch.setattr(sn, "get_chat_model", lambda *a, **k: _FakeChat())

    class _StoreWithExisting:
        def list_approved(self):
            return [{"version": 1, "document": {"tables": []}}]

    class _InspectCounter:
        def __init__(self):
            self.calls = 0

        def _run(self):
            self.calls += 1
            return {"tables": []}

    inspect = _InspectCounter()
    runner = SchemaAgentRunner(
        schema_docs_store=_StoreWithExisting(),
        schema_inspect_tool=inspect,
    )
    runner.start(session_id="thread-existing")
    assert inspect.calls == 0


def test_schema_partial_update_keeps_other_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings,
        "schema_docs_path",
        str(tmp_path / "schema_docs.json"),
    )

    from memory.schema_docs_store import SchemaDocsStore

    store = SchemaDocsStore(path=str(tmp_path / "schema_docs.json"))
    store.save_approved(
        session_id="seed",
        document={
            "tables": [
                {"table_name": "film", "description": "old film", "columns": []},
                {"table_name": "actor", "description": "old actor", "columns": []},
            ]
        },
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
                                "description": "new film",
                                "columns": [{"name": "film_id", "description": "id"}],
                            }
                        ]
                    }
                )
            )

    import graph.schema_nodes as sn

    monkeypatch.setattr(sn, "get_chat_model", lambda *a, **k: _FakeChat())

    class _InspectCounter:
        def __init__(self):
            self.calls = 0

        def _run(self):
            self.calls += 1
            return {"tables": []}

    inspect = _InspectCounter()
    runner = SchemaAgentRunner(schema_inspect_tool=inspect)
    _ = runner.start(session_id="thread-partial")
    out = runner.resume(session_id="thread-partial", human_feedback={"action": "approve"})
    assert out.get("status") == "persisted"

    latest = store.latest()
    assert latest is not None
    names = {t.get("table_name") for t in latest["document"]["tables"]}
    assert names == {"film", "actor"}
    film = [t for t in latest["document"]["tables"] if t.get("table_name") == "film"][0]
    assert film["description"] == "new film"
