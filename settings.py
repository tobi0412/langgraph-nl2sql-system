"""Centralized project settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application and service settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/dvdrental",
        alias="DATABASE_URL",
    )
    db_connect_timeout: int = Field(default=5, alias="DB_CONNECT_TIMEOUT")

    llm_model: str = Field(default="gpt-4.1-mini", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://sa-llmproxy.it.itba.edu.ar", alias="LLM_BASE_URL")

    enable_schema_agent: bool = Field(default=True, alias="ENABLE_SCHEMA_AGENT")
    enable_query_agent: bool = Field(default=True, alias="ENABLE_QUERY_AGENT")


settings = Settings()
