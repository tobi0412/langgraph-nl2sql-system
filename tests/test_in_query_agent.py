"""Integration tests for Query Agent iteration 4."""

import psycopg
import pytest

import graph.query_nodes as query_nodes_module
from agents.query_agent import QueryAgent
from tools.mcp_sql_tool import MCPSQLQueryTool


def _schema_entry():
    return {
        "document": {
            "tables": [
                {"table_name": "film", "columns": [{"name": "film_id"}, {"name": "title"}]},
                {"table_name": "actor", "columns": [{"name": "actor_id"}, {"name": "first_name"}]},
                {"table_name": "rental", "columns": [{"name": "rental_id"}, {"name": "customer_id"}]},
            ]
        }
    }


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch):
    monkeypatch.setattr(query_nodes_module.settings, "llm_api_key", "")


def _require_db() -> None:
    try:
        MCPSQLQueryTool()._run("SELECT 1;")
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB unavailable for QueryAgent integration tests: {exc}")


def _build_agent(monkeypatch) -> QueryAgent:
    monkeypatch.setattr(
        query_nodes_module.SchemaDocsStore,
        "latest",
        lambda self: _schema_entry(),
    )
    return QueryAgent()


def test_in_query_agent_returns_sql_sample_and_explanation(monkeypatch):
    _require_db()
    result = _build_agent(monkeypatch).run("Mostrame registros de film")
    assert result["status"] == "ok"
    assert result["sql_final"].lower().startswith("select")
    assert isinstance(result["sample"]["rows"], list)
    assert result["explanation"]


def test_in_query_agent_handles_aggregate_question(monkeypatch):
    _require_db()
    result = _build_agent(monkeypatch).run("Cuantos registros hay en rental?")
    assert result["status"] == "ok"
    assert "count(" in result["sql_final"].lower()
    assert "total" in [c.lower() for c in result["sample"]["columns"]]


def test_in_query_agent_handles_actor_question(monkeypatch):
    _require_db()
    result = _build_agent(monkeypatch).run("Lista actores")
    assert result["status"] == "ok"
    assert "from actor" in result["sql_final"].lower()


def test_in_query_agent_asks_for_clarification_on_ambiguous_question(monkeypatch):
    _require_db()
    result = _build_agent(monkeypatch).run("Dame informacion")
    assert result["status"] == "needs_clarification"
    assert result["clarification_question"]


def test_in_query_agent_blocks_without_schema_docs(monkeypatch):
    _require_db()
    monkeypatch.setattr(query_nodes_module.SchemaDocsStore, "latest", lambda self: None)
    result = QueryAgent().run("Lista actores")
    assert result["status"] == "blocked_missing_schema"
    assert result["sql_final"] is None
