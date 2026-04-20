"""HTTP microservice for MCP tools."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from db import check_database_connection
from tools.service import execute_read_only_sql_query, inspect_schema

app = FastAPI(
    title="langgraph-nl2sql-tools",
    description="HTTP service for MCP schema/sql tools.",
    version="0.1.0",
)


class SQLQueryRequest(BaseModel):
    sql: str = Field(description="Read-only SQL query")


class SchemaInspectRequest(BaseModel):
    table_names: list[str] | None = Field(default=None)
    include_samples: bool = Field(default=False)
    sample_rows: int = Field(default=3, ge=3, le=5)


@app.get("/health")
def health() -> dict[str, str]:
    check_database_connection()
    return {"status": "healthy"}


@app.post("/tools/sql-query")
def sql_query(payload: SQLQueryRequest) -> dict[str, object]:
    return execute_read_only_sql_query(payload.sql)


@app.post("/tools/schema-inspect")
def schema_inspect(payload: SchemaInspectRequest) -> dict[str, object]:
    return inspect_schema(
        table_names=payload.table_names,
        include_samples=payload.include_samples,
        sample_rows=payload.sample_rows,
    )
