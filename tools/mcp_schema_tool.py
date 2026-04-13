"""MCP schema inspection tool."""

import logging
import uuid

import psycopg
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from settings import settings

logger = logging.getLogger(__name__)


class MCPSchemaInspectInput(BaseModel):
    """Input schema for schema inspection."""

    pass


class MCPSchemaInspectTool(BaseTool):
    """MCP tool for inspecting real PostgreSQL schema metadata."""

    name: str = "mcp_schema_inspect"
    description: str = (
        "Inspects the database schema and returns available tables, columns, "
        "PK/FK, and constraints."
    )
    args_schema: type[BaseModel] = MCPSchemaInspectInput

    def _run(self) -> dict[str, object]:
        """Inspect real public schema metadata for agents."""
        call_id = str(uuid.uuid4())
        logger.info(
            "mcp_tool_call",
            extra={"tool": "mcp_schema_inspect", "call_id": call_id, "request": {}},
        )

        tables_query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name;
        """

        columns_query = """
        SELECT
            c.table_name,
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
        ORDER BY c.table_name, c.ordinal_position;
        """

        pk_query = """
        SELECT
            tc.table_name,
            kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public' AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY tc.table_name, kcu.ordinal_position;
        """

        fk_query = """
        SELECT
            tc.table_name,
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'public' AND tc.constraint_type = 'FOREIGN KEY'
        ORDER BY tc.table_name, kcu.column_name;
        """

        constraints_query = """
        SELECT
            tc.table_name,
            tc.constraint_name,
            tc.constraint_type
        FROM information_schema.table_constraints tc
        WHERE tc.table_schema = 'public'
        ORDER BY tc.table_name, tc.constraint_name;
        """

        with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout) as conn:
            with conn.cursor() as cur:
                cur.execute(tables_query)
                table_rows = cur.fetchall()

                cur.execute(columns_query)
                column_rows = cur.fetchall()

                cur.execute(pk_query)
                pk_rows = cur.fetchall()

                cur.execute(fk_query)
                fk_rows = cur.fetchall()

                cur.execute(constraints_query)
                constraint_rows = cur.fetchall()

        by_table: dict[str, dict[str, object]] = {
            table_name: {
                "table_name": table_name,
                "columns": [],
                "primary_keys": [],
                "foreign_keys": [],
                "constraints": [],
            }
            for (table_name,) in table_rows
        }

        for table_name, column_name, data_type, is_nullable, column_default in column_rows:
            by_table.setdefault(
                table_name,
                {
                    "table_name": table_name,
                    "columns": [],
                    "primary_keys": [],
                    "foreign_keys": [],
                    "constraints": [],
                },
            )
            by_table[table_name]["columns"].append(
                {
                    "name": column_name,
                    "data_type": data_type,
                    "is_nullable": is_nullable == "YES",
                    "default": column_default,
                }
            )

        for table_name, column_name in pk_rows:
            by_table[table_name]["primary_keys"].append(column_name)

        for table_name, column_name, foreign_table_name, foreign_column_name in fk_rows:
            by_table[table_name]["foreign_keys"].append(
                {
                    "column": column_name,
                    "references_table": foreign_table_name,
                    "references_column": foreign_column_name,
                }
            )

        for table_name, constraint_name, constraint_type in constraint_rows:
            by_table[table_name]["constraints"].append(
                {"name": constraint_name, "type": constraint_type}
            )

        response = {
            "tool": "mcp_schema_inspect",
            "call_id": call_id,
            "table_count": len(by_table),
            "tables": list(by_table.values()),
        }

        logger.info(
            "mcp_tool_response",
            extra={"tool": "mcp_schema_inspect", "call_id": call_id, "response": response},
        )
        return response

    async def _arun(self) -> dict[str, object]:
        return self._run()
