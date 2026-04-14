"""Unit tests: schema docs store."""

from pathlib import Path

from memory.schema_docs_store import SchemaDocsStore


def test_schema_docs_store_roundtrip(tmp_path: Path):
    path = tmp_path / "docs.json"
    store = SchemaDocsStore(path=str(path))
    entry = store.save_approved(
        user_id="u1",
        session_id="s1",
        document={"tables": [{"table_name": "film", "description": "x", "columns": []}]},
    )
    assert entry["version"] == 1
    assert entry["document"]["tables"][0]["table_name"] == "film"

    again = store.save_approved(
        user_id="u1",
        session_id="s2",
        document={"tables": []},
    )
    assert again["version"] == 2
