"""Integration tests for MCP schema/sql tools."""

import psycopg
import pytest

from tools.mcp_schema_tool import MCPSchemaInspectTool
from tools.mcp_sql_tool import MCPSQLQueryTool


def _require_tools_db() -> None:
    try:
        MCPSQLQueryTool()._run("SELECT 1;")
    except psycopg.OperationalError as exc:
        pytest.skip(f"DB unavailable for MCP integration tests: {exc}")


def test_in_mcp_schema_inspect_returns_metadata():
    _require_tools_db()
    payload = MCPSchemaInspectTool()._run()
    assert payload["tool"] == "mcp_schema_inspect"
    assert payload["table_count"] >= 1
    table_names = {table["table_name"] for table in payload["tables"]}
    assert {"film", "actor", "rental"}.issubset(table_names)


def test_in_mcp_schema_inspect_supports_multiple_table_samples():
    _require_tools_db()
    payload = MCPSchemaInspectTool()._run(table_names=["film", "actor"], sample_rows=3)
    table_names = {table["table_name"] for table in payload["tables"]}
    assert table_names == {"film", "actor"}
    for table in payload["tables"]:
        assert "sample" in table
        assert table["sample"]["limit"] == 3
        assert len(table["sample"]["rows"]) <= 3


def test_in_mcp_sql_query_select_returns_rows():
    _require_tools_db()
    payload = MCPSQLQueryTool()._run("SELECT film_id FROM film LIMIT 1;")
    assert payload["tool"] == "mcp_sql_query"
    assert payload["row_count"] >= 0
    assert "film_id" in payload["columns"]


def test_in_mcp_sql_query_rejects_delete():
    with pytest.raises(ValueError, match="Only read-only SQL|Disallowed statement"):
        MCPSQLQueryTool()._run("DELETE FROM film;")
