"""MCP tool for read-only SQL execution."""

import logging
import uuid

import psycopg
from langsmith import traceable
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from settings import settings
from tools.sql_guard import validate_read_only_sql

logger = logging.getLogger(__name__)


class MCPSQLQueryInput(BaseModel):
    """Input schema for read-only SQL query."""

    sql: str = Field(description="Read-only SQL query (SELECT)")


class MCPSQLQueryTool(BaseTool):
    """MCP tool that executes safe read-only SQL."""

    name: str = "mcp_sql_query"
    description: str = (
        "Executes read-only SQL queries against the database. "
        "Blocks write or destructive statements."
    )
    args_schema: type[BaseModel] = MCPSQLQueryInput

    @traceable(run_type="tool", name="mcp_sql_query", tags=["mcp-tool", "sql"])
    def _run(self, sql: str) -> dict[str, object]:
        """Execute read-only SQL and return a serializable payload."""
        call_id = str(uuid.uuid4())
        normalized = validate_read_only_sql(sql)

        logger.info(
            "mcp_tool_call",
            extra={
                "tool": "mcp_sql_query",
                "call_id": call_id,
                "request": {"sql": sql},
            },
        )

        with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                columns = [desc.name for desc in cur.description] if cur.description else []

        response = {
            "tool": "mcp_sql_query",
            "call_id": call_id,
            "sql": sql,
            "normalized_sql": normalized,
            "row_count": len(rows),
            "columns": columns,
            "rows": [list(row) for row in rows],
        }

        logger.info(
            "mcp_tool_response",
            extra={"tool": "mcp_sql_query", "call_id": call_id, "response": response},
        )
        return response

    async def _arun(self, sql: str) -> dict[str, object]:
        return self._run(sql)
