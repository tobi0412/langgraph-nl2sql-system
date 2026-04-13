"""SQL safety validations for MCP tools."""

import re

FORBIDDEN_SQL_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
)


def _strip_sql_comments(sql: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    no_line = re.sub(r"--.*?$", " ", no_block, flags=re.MULTILINE)
    return no_line


def validate_read_only_sql(sql: str) -> str:
    """Validate read-only SQL and return a normalized version."""
    if not sql or not sql.strip():
        raise ValueError("Empty SQL.")

    normalized = _strip_sql_comments(sql).strip().lower()
    if not normalized.startswith("select"):
        raise ValueError("Only read-only SQL (SELECT) is allowed.")

    for keyword in FORBIDDEN_SQL_KEYWORDS:
        if re.search(rf"\b{keyword}\b", normalized):
            raise ValueError(f"Disallowed statement detected: {keyword.upper()}")

    return normalized
