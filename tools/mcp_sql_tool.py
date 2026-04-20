"""MCP tool for read-only SQL execution."""

import logging
import uuid

from langsmith import traceable
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from settings import settings
from tools.http_client import call_tools_service
from tools.service import execute_read_only_sql_query
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
        validate_read_only_sql(sql)

        logger.info(
            "mcp_tool_call",
            extra={
                "tool": "mcp_sql_query",
                "call_id": call_id,
                "request": {"sql": sql},
            },
        )
        if settings.mcp_tools_mode.lower() == "http":
            response = call_tools_service("/tools/sql-query", {"sql": sql})
        else:
            response = execute_read_only_sql_query(sql)

        logger.info(
            "mcp_tool_response",
            extra={"tool": "mcp_sql_query", "call_id": call_id, "response": response},
        )
        return response

    async def _arun(self, sql: str) -> dict[str, object]:
        return self._run(sql)
