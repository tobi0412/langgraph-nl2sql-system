"""Unit tests: schema document merge (partial table updates)."""

from graph.schema_nodes import _merge_schema_documents, _merge_table_entry


def test_merge_keeps_other_tables_when_patch_has_one_table():
    base = {
        "tables": [
            {
                "table_name": "film",
                "description": "Films",
                "columns": [{"name": "film_id", "description": "id"}],
            },
            {
                "table_name": "actor",
                "description": "old actor",
                "columns": [{"name": "actor_id", "description": "pk"}],
            },
        ]
    }
    patch = {
        "tables": [
            {
                "table_name": "actor",
                "description": "new actor desc",
                "columns": [{"name": "actor_id", "description": "primary key"}],
            }
        ]
    }
    out = _merge_schema_documents(base, patch)
    names = [t["table_name"] for t in out["tables"]]
    assert names == ["film", "actor"]
    film = next(t for t in out["tables"] if t["table_name"] == "film")
    assert film["description"] == "Films"
    actor = next(t for t in out["tables"] if t["table_name"] == "actor")
    assert actor["description"] == "new actor desc"


def test_merge_invalid_patch_tables_keeps_base():
    base = {"tables": [{"table_name": "film", "description": "x", "columns": []}]}
    assert _merge_schema_documents(base, {"tables": None}) == base
    assert _merge_schema_documents(base, {})["tables"] == base["tables"]


def test_merge_empty_patch_tables_list_keeps_base_tables():
    base = {"tables": [{"table_name": "film", "description": "x", "columns": []}]}
    out = _merge_schema_documents(base, {"tables": [], "note": "noop"})
    assert len(out["tables"]) == 1
    assert out["note"] == "noop"


def test_merge_table_entry_preserves_columns_when_patch_omits_columns():
    base = {
        "table_name": "actor",
        "description": "Actors",
        "columns": [
            {"name": "actor_id", "description": "pk"},
            {"name": "first_name", "description": "first"},
        ],
    }
    patch = {"table_name": "actor", "description": "Actors catalog"}
    out = _merge_table_entry(base, patch)
    assert out["description"] == "Actors catalog"
    assert len(out["columns"]) == 2
