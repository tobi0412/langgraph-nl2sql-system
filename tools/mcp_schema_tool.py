"""Herramienta MCP de esquema (base)."""

from db import get_existing_tables


def inspect_core_tables() -> dict[str, list[str]]:
    """Inspecciona tablas clave esperadas de DVD Rental."""
    required = ["film", "actor", "rental"]
    existing = sorted(get_existing_tables(required))
    return {"required": required, "existing": existing}
