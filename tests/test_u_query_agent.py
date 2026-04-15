"""Unit tests for Query Agent iteration 4."""

import graph.query_nodes as query_nodes_module
import pytest
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


def test_u_query_agent_maps_intent_to_film():
    state = query_nodes_module.planner_node(
        {
            "question": "Lista titulos de film",
            "schema_context": {"film": ["film_id", "title"]},
        }
    )
    assert state["intent"] == "list"
    assert state["candidate_tables"] == ["film"]
    assert state["needs_clarification"] is False


def test_u_query_agent_validator_rejects_risky_sql():
    state = query_nodes_module.critic_node(
        {
            "question": "Cuantos films hay?",
            "schema_context": {"film": ["film_id", "title"]},
            "sql_candidate": "DELETE FROM film;",
            "needs_clarification": False,
            "clarification_question": None,
        }
    )
    validator = state["validator"]
    assert validator["approved"] is False
    assert validator["risk_level"] == "high"
    assert any(
        "read-only" in issue.lower() or "disallowed" in issue.lower()
        for issue in validator["issues"]
    )


def test_u_query_agent_requests_clarification_when_ambiguous(monkeypatch):
    monkeypatch.setattr(
        query_nodes_module.SchemaDocsStore,
        "latest",
        lambda self: _schema_entry(),
    )
    monkeypatch.setattr(
        MCPSQLQueryTool,
        "_run",
        lambda self, sql: {"row_count": 1, "columns": ["x"], "rows": [[1]]},
    )
    agent = QueryAgent()
    result = agent.run("Dame datos")
    assert result["status"] == "needs_clarification"
    assert result["clarification_question"]
    assert result["sql_final"] is None
    assert isinstance(result["limitations"], list)


def test_u_query_agent_blocks_when_schema_is_missing(monkeypatch):
    monkeypatch.setattr(query_nodes_module.SchemaDocsStore, "latest", lambda self: None)
    monkeypatch.setattr(
        MCPSQLQueryTool,
        "_run",
        lambda self, sql: {"row_count": 1, "columns": ["x"], "rows": [[1]]},
    )
    agent = QueryAgent()
    result = agent.run("Lista actores")
    assert result["status"] == "blocked_missing_schema"
    assert result["sql_final"] is None
    assert "schema agent" in result["limitations"][1].lower()
