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

        def execute(self, _query):
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

    monkeypatch.setattr("tools.mcp_schema_tool.psycopg.connect", lambda *args, **kwargs: DummyConn())
    payload = MCPSchemaInspectTool()._run()

    assert payload["tool"] == "mcp_schema_inspect"
    assert payload["table_count"] == 1
    assert payload["tables"][0]["table_name"] == "film"
    assert payload["tables"][0]["columns"][0]["name"] == "film_id"
