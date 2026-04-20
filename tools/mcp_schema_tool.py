"""MCP schema inspection tool."""

import logging
import uuid

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from settings import settings
from tools.http_client import call_tools_service
from tools.service import inspect_schema

logger = logging.getLogger(__name__)


class MCPSchemaInspectInput(BaseModel):
    """Input schema for schema inspection."""

    table_names: list[str] | None = Field(
        default=None,
        description="Optional list of specific public tables to inspect.",
    )
    include_samples: bool = Field(
        default=False,
        description="Include sample rows in each returned table object.",
    )
    sample_rows: int = Field(
        default=3,
        ge=3,
        le=5,
        description="Number of sample rows per table (3..5).",
    )


class MCPSchemaInspectTool(BaseTool):
    """MCP tool for inspecting real PostgreSQL schema metadata."""

    name: str = "mcp_schema_inspect"
    description: str = (
        "Inspects the database schema and returns available tables, columns, "
        "PK/FK, constraints, and optional 3..5 sample rows per table."
    )
    args_schema: type[BaseModel] = MCPSchemaInspectInput

    def _run(
        self,
        table_names: list[str] | None = None,
        include_samples: bool = False,
        sample_rows: int = 3,
    ) -> dict[str, object]:
        """Inspect real public schema metadata for agents."""
        call_id = str(uuid.uuid4())
        selected_tables = [name.strip() for name in (table_names or []) if name and name.strip()]
        selected_tables = list(dict.fromkeys(selected_tables))
        include_samples_effective = include_samples or bool(selected_tables)
        logger.info(
            "mcp_tool_call",
            extra={
                "tool": "mcp_schema_inspect",
                "call_id": call_id,
                "request": {
                    "table_names": selected_tables,
                    "include_samples": include_samples_effective,
                    "sample_rows": sample_rows,
                },
            },
        )
        if settings.mcp_tools_mode.lower() == "http":
            response = call_tools_service(
                "/tools/schema-inspect",
                {
                    "table_names": selected_tables,
                    "include_samples": include_samples_effective,
                    "sample_rows": sample_rows,
                },
            )
        else:
            response = inspect_schema(
                table_names=selected_tables,
                include_samples=include_samples_effective,
                sample_rows=sample_rows,
            )

        logger.info(
            "mcp_tool_response",
            extra={"tool": "mcp_schema_inspect", "call_id": call_id, "response": response},
        )
        return response

    async def _arun(
        self,
        table_names: list[str] | None = None,
        include_samples: bool = False,
        sample_rows: int = 3,
    ) -> dict[str, object]:
        return self._run(
            table_names=table_names,
            include_samples=include_samples,
            sample_rows=sample_rows,
        )
