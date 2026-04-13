# langgraph-nl2sql-system

Technical baseline from Iteration 1 for a multi-agent NL2SQL system with LangGraph.

## Current Status (Iteration 1)

- Initial project structure created according to the assignment:
  - `agents/schema_agent.py`
  - `agents/query_agent.py`
  - `graph/workflow.py`
  - `memory/persistent_store.py`
  - `memory/session_store.py`
  - `tools/mcp_schema_tool.py`
  - `tools/mcp_sql_tool.py`
  - `prompts/`
  - `tests/`
- Minimal API with `GET /health`.
- Centralized environment configuration in `settings.py` and `.env.example`.
- PostgreSQL connectivity validated from the healthcheck.
- Base Docker setup (`Dockerfile`, `docker-compose.yml`, `.dockerignore`).

## Environment Configuration

1. Copy environment variables:
   - `cp .env.example .env`
2. Adjust `DATABASE_URL` if needed.

Key variables:

- DB: `DATABASE_URL`, `DB_CONNECT_TIMEOUT`
- LLM: `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`
- App: `APP_HOST`, `APP_PORT`, `APP_ENV`, `APP_LOG_LEVEL`
- Flags: `ENABLE_SCHEMA_AGENT`, `ENABLE_QUERY_AGENT`

## Local Run

```bash
pip install -e ".[dev]"
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Healthcheck:

- [http://localhost:8000/health](http://localhost:8000/health)

## Docker

Build:

```bash
docker compose build
```

Run:

```bash
docker compose up
```

Healthcheck:

- [http://localhost:8000/health](http://localhost:8000/health)

## Dataset DVD Rental

- For minimal local bootstrap (iteration 1), a schema with tables `film`, `actor`, and `rental` is loaded automatically.
- To use the full official dataset, follow `scripts/bootstrap_dvdrental.md`.

## Iteration Tests

```bash
pytest tests/test_u_settings.py
pytest tests/test_in_db_connectivity.py
pytest tests/test_f_health.py
```