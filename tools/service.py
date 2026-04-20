"""Core implementations for MCP SQL and schema tools."""

from __future__ import annotations

import uuid

import psycopg
from psycopg import sql

from settings import settings
from tools.sql_guard import validate_read_only_sql


def execute_read_only_sql_query(sql_query: str) -> dict[str, object]:
    """Execute read-only SQL and return a JSON-serializable payload."""
    call_id = str(uuid.uuid4())
    normalized = validate_read_only_sql(sql_query)

    with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_query)
            rows = cur.fetchall()
            columns = [desc.name for desc in cur.description] if cur.description else []

    return {
        "tool": "mcp_sql_query",
        "call_id": call_id,
        "sql": sql_query,
        "normalized_sql": normalized,
        "row_count": len(rows),
        "columns": columns,
        "rows": [list(row) for row in rows],
    }


def inspect_schema(
    *,
    table_names: list[str] | None = None,
    include_samples: bool = False,
    sample_rows: int = 3,
) -> dict[str, object]:
    """Inspect public schema metadata and optional sample rows."""
    call_id = str(uuid.uuid4())
    selected_tables = [name.strip() for name in (table_names or []) if name and name.strip()]
    selected_tables = list(dict.fromkeys(selected_tables))
    include_samples_effective = include_samples or bool(selected_tables)

    tables_query = """
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
    """
    if selected_tables:
        tables_query += " AND table_name = ANY(%s)"
        tables_params: tuple[object, ...] = (selected_tables,)
    else:
        tables_params = ()
    tables_query += " ORDER BY table_name;"

    columns_query = """
    SELECT
        c.table_name,
        c.column_name,
        c.data_type,
        c.is_nullable,
        c.column_default
    FROM information_schema.columns c
    WHERE c.table_schema = 'public'
    """
    if selected_tables:
        columns_query += " AND c.table_name = ANY(%s)"
    columns_query += """
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
    """
    if selected_tables:
        pk_query += " AND tc.table_name = ANY(%s)"
    pk_query += """
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
    """
    if selected_tables:
        fk_query += " AND tc.table_name = ANY(%s)"
    fk_query += """
    ORDER BY tc.table_name, kcu.column_name;
    """

    constraints_query = """
    SELECT
        tc.table_name,
        tc.constraint_name,
        tc.constraint_type
    FROM information_schema.table_constraints tc
    WHERE tc.table_schema = 'public'
    """
    if selected_tables:
        constraints_query += " AND tc.table_name = ANY(%s)"
    constraints_query += """
    ORDER BY tc.table_name, tc.constraint_name;
    """

    with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout) as conn:
        with conn.cursor() as cur:
            cur.execute(tables_query, tables_params)
            table_rows = cur.fetchall()

            cur.execute(columns_query, tables_params if selected_tables else ())
            column_rows = cur.fetchall()

            cur.execute(pk_query, tables_params if selected_tables else ())
            pk_rows = cur.fetchall()

            cur.execute(fk_query, tables_params if selected_tables else ())
            fk_rows = cur.fetchall()

            cur.execute(constraints_query, tables_params if selected_tables else ())
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

    if include_samples_effective:
        with psycopg.connect(settings.database_url, connect_timeout=settings.db_connect_timeout) as conn:
            with conn.cursor() as cur:
                for table_name, payload in by_table.items():
                    query = sql.SQL("SELECT * FROM {} LIMIT %s").format(sql.Identifier(table_name))
                    cur.execute(query, (sample_rows,))
                    sample_rows_data = cur.fetchall()
                    columns = [desc.name for desc in cur.description] if cur.description else []
                    payload["sample"] = {
                        "columns": columns,
                        "rows": [list(row) for row in sample_rows_data],
                        "row_count": len(sample_rows_data),
                        "limit": sample_rows,
                    }

    return {
        "tool": "mcp_schema_inspect",
        "call_id": call_id,
        "table_count": len(by_table),
        "tables": list(by_table.values()),
        "filters": {
            "table_names": selected_tables,
            "include_samples": include_samples_effective,
            "sample_rows": sample_rows,
        },
    }
