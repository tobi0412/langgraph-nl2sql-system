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
    preferences_database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5434/user_preferences",
        alias="PREFERENCES_DATABASE_URL",
    )
    db_connect_timeout: int = Field(default=5, alias="DB_CONNECT_TIMEOUT")
    mcp_tools_mode: str = Field(default="local", alias="MCP_TOOLS_MODE")
    mcp_tools_base_url: str = Field(default="http://localhost:8010", alias="MCP_TOOLS_BASE_URL")
    mcp_tools_timeout_seconds: float = Field(default=15.0, alias="MCP_TOOLS_TIMEOUT_SECONDS")

    llm_model: str = Field(default="gpt-5-nano", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://sa-llmproxy.it.itba.edu.ar", alias="LLM_BASE_URL")

    enable_schema_agent: bool = Field(default=True, alias="ENABLE_SCHEMA_AGENT")
    enable_query_agent: bool = Field(default=True, alias="ENABLE_QUERY_AGENT")

    schema_docs_path: str = Field(
        default="data/schema_docs.json",
        alias="SCHEMA_DOCS_PATH",
    )
    schema_agent_max_iterations: int = Field(
        default=15,
        alias="SCHEMA_AGENT_MAX_ITERATIONS",
    )

    user_preferences_path: str = Field(
        default="data/user_preferences.json",
        alias="USER_PREFERENCES_PATH",
    )
    # postgres: table in PREFERENCES_DATABASE_URL. json: USER_PREFERENCES_PATH file.
    preferences_store_backend: str = Field(default="postgres", alias="PREFERENCES_STORE_BACKEND")
    session_memory_path: str = Field(
        default="data/session_memory.json",
        alias="SESSION_MEMORY_PATH",
    )
    session_memory_ttl_seconds: int = Field(
        default=3600,
        alias="SESSION_MEMORY_TTL_SECONDS",
    )
    working_session_token_limit: int = Field(
        default=2000,
        alias="WORKING_SESSION_TOKEN_LIMIT",
    )

    # LangSmith (LangChain/LangGraph tracing — set LANGCHAIN_TRACING_V2=true)
    langchain_tracing_v2: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    langchain_api_key: str = Field(default="", alias="LANGCHAIN_API_KEY")
    langchain_project: str = Field(default="", alias="LANGCHAIN_PROJECT")
    langchain_endpoint: str = Field(
        default="https://api.smith.langchain.com",
        alias="LANGCHAIN_ENDPOINT",
    )

    @property
    def sql_dialect(self) -> str:
        """Human-readable SQL engine name derived from ``database_url``.

        Used by the Query Agent prompts so the LLM generates dialect-correct
        syntax (functions, quoting, pagination, etc.) without hard-coding
        a single engine in the prompt text.
        """
        url = (self.database_url or "").strip().lower()
        if url.startswith(("postgres://", "postgresql://", "postgresql+")):
            return "PostgreSQL"
        if url.startswith(("mysql://", "mysql+", "mariadb://", "mariadb+")):
            return "MySQL"
        if url.startswith(("sqlite:", "sqlite+")):
            return "SQLite"
        if url.startswith(("mssql://", "mssql+", "sqlserver://")):
            return "Microsoft SQL Server"
        if url.startswith(("oracle://", "oracle+")):
            return "Oracle"
        return "PostgreSQL"


settings = Settings()
