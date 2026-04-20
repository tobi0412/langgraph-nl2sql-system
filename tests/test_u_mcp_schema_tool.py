"""Unit tests for schema inspector normalization."""

from tools.mcp_schema_tool import MCPSchemaInspectTool


def test_u_mcp_schema_inspect_shape(monkeypatch):
    class DummyCursor:
        def __init__(self) -> None:
            self._index = 0
            self._results = [
                [("film",)],
                [("film", "film_id", "integer", "NO", None)],
                [("film", "film_id")],
                [],
                [("film", "film_pkey", "PRIMARY KEY")],
            ]

        def execute(self, _query, _params=None):
            return None

        def fetchall(self):
            res = self._results[self._index]
            self._index += 1
            return res

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    class DummyConn:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    shared_cursor = DummyCursor()
    monkeypatch.setattr(
        "tools.service.psycopg.connect",
        lambda *args, **kwargs: DummyConn(shared_cursor),
    )
    payload = MCPSchemaInspectTool()._run()

    assert payload["tool"] == "mcp_schema_inspect"
    assert payload["table_count"] == 1
    assert payload["tables"][0]["table_name"] == "film"
    assert payload["tables"][0]["columns"][0]["name"] == "film_id"


def test_u_mcp_schema_inspect_supports_table_filter_and_samples(monkeypatch):
    class DummyCursor:
        def __init__(self) -> None:
            self._index = 0
            self.description = [type("D", (), {"name": "film_id"})(), type("D", (), {"name": "title"})()]
            self._results = [
                [("film",), ("actor",)],
                [("film", "film_id", "integer", "NO", None), ("actor", "actor_id", "integer", "NO", None)],
                [("film", "film_id"), ("actor", "actor_id")],
                [],
                [("film", "film_pkey", "PRIMARY KEY"), ("actor", "actor_pkey", "PRIMARY KEY")],
                [(1, "ACADEMY DINOSAUR"), (2, "ACE GOLDFINGER"), (3, "ADAPTATION HOLES")],
                [(1, "PENELOPE"), (2, "NICK"), (3, "ED")],
            ]

        def execute(self, _query, _params=None):
            return None

        def fetchall(self):
            res = self._results[self._index]
            self._index += 1
            return res

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    class DummyConn:
        def cursor(self):
            return DummyCursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("tools.service.psycopg.connect", lambda *args, **kwargs: DummyConn())
    payload = MCPSchemaInspectTool()._run(table_names=["film", "actor"], sample_rows=3)

    assert payload["table_count"] == 2
    assert payload["filters"]["table_names"] == ["film", "actor"]
    assert payload["filters"]["include_samples"] is True
    assert payload["filters"]["sample_rows"] == 3
    first_table = payload["tables"][0]
    assert "sample" in first_table
    assert first_table["sample"]["limit"] == 3
