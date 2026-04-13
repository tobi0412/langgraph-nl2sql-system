"""Unit tests for settings."""

from settings import Settings


def test_u_settings_defaults():
    settings = Settings()
    assert settings.app_port == 8000
    assert settings.app_host == "0.0.0.0"
    assert settings.enable_schema_agent is True
    assert settings.enable_query_agent is True


def test_u_settings_env_override(monkeypatch):
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/custom")
    settings = Settings()
    assert settings.app_port == 9000
    assert settings.database_url == "postgresql://u:p@localhost:5432/custom"
