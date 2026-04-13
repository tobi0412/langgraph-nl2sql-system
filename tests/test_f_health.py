"""Functional tests for /health endpoint."""

from fastapi.testclient import TestClient

from main import app


def test_f_health_returns_200(monkeypatch):
    monkeypatch.setattr("main.check_database_connection", lambda: True)
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
