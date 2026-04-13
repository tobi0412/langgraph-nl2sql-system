# langgraph-nl2sql-system

Base tecnica de la Iteracion 1 para un sistema NL2SQL multiagente con LangGraph.

## Estado actual (Iteracion 1)

- Estructura inicial del proyecto creada segun consigna:
  - `agents/schema_agent.py`
  - `agents/query_agent.py`
  - `graph/workflow.py`
  - `memory/persistent_store.py`
  - `memory/session_store.py`
  - `tools/mcp_schema_tool.py`
  - `tools/mcp_sql_tool.py`
  - `prompts/`
  - `tests/`
- API minima con `GET /health`.
- Configuracion centralizada por entorno en `settings.py` y `.env.example`.
- Conexion PostgreSQL validada desde healthcheck.
- Dockerizacion base (`Dockerfile`, `docker-compose.yml`, `.dockerignore`).

## Configuracion de entorno

1. Copiar variables:
   - `cp .env.example .env`
2. Ajustar `DATABASE_URL` si corresponde.

Variables clave:

- DB: `DATABASE_URL`, `DB_CONNECT_TIMEOUT`
- LLM: `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`
- App: `APP_HOST`, `APP_PORT`, `APP_ENV`, `APP_LOG_LEVEL`
- Flags: `ENABLE_SCHEMA_AGENT`, `ENABLE_QUERY_AGENT`

## Ejecucion local

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

- Para bootstrap local minimo (iteracion 1), se carga automaticamente un esquema con tablas `film`, `actor` y `rental`.
- Para usar el dataset oficial completo, seguir `scripts/bootstrap_dvdrental.md`.

## Tests de iteracion

```bash
pytest tests/test_u_settings.py
pytest tests/test_in_db_connectivity.py
pytest tests/test_f_health.py
```