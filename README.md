# langgraph-nl2sql-system

Technical baseline from Iteration 1 for a multi-agent NL2SQL system with LangGraph.

## Current Status

### Iteration 1 (base)

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

### Iteration 3 — Schema Agent (LangGraph + HITL)

- LangGraph flow: `agent` ↔ `tools` (`mcp_schema_inspect`) → `format_draft` → `human_gate` (interrupt) → `persist_approved`.
- LLM via LiteLLM-compatible OpenAI API: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.
- Approved schema docs append-only JSON: `SCHEMA_DOCS_PATH` (default `data/schema_docs.json`).
- Programmatic API: `SchemaAgentRunner.start(...)` then `SchemaAgentRunner.resume(session_id=..., human_feedback={"action": "approve"|"edit"|"reject", ...})`.
- Modules: `graph/schema_*.py`, `memory/schema_docs_store.py`, `llm/chat_model.py`, `prompts/schema_agent.py`.

Docker Compose loads `.env` into the `app` service so `LLM_*` and `SCHEMA_*` are available in the container.

### Iteration 4/6 — Query Agent + Tabs Architecture

- `QueryAgent` implements single-pass NL2SQL (intent + SQL + validation payload) with read-only execution through `mcp_sql_query`.
- `Schema Agent` and `Query Agent` run in separate UI tabs and do not hand off directly at runtime.
- Integration is done through persistent schema descriptions (`SchemaDocsStore`, `SCHEMA_DOCS_PATH`).
- `QueryAgent` never calls schema inspection tools as fallback.
- If no approved schema descriptions exist, `QueryAgent` returns `blocked_missing_schema` and asks the user to run `Schema Agent` first.

### Streamlit (UI simple)

Inspirada en `demos-estudiantes-main/EJ02-ReAct-LangGraph/spec-ui.md`: variables `API_BASE_URL` / `API_TIMEOUT`, healthcheck del API y flujo claro en sidebar + panel principal.

Local (desde la raiz del repo, con `.env` y dependencias UI):

```bash
pip install -e ".[ui]"
streamlit run streamlit_app/app.py --server.port 8501
```

Con Docker Compose (API + Postgres + UI):

```bash
docker compose up --build
```

Solo **Postgres + Streamlit** (el Schema Agent no necesita el contenedor `app`; el boton *Verificar API* fallara hasta que levantes `app`):

```bash
docker compose up postgres streamlit --build
```

- API: [http://localhost:8000/health](http://localhost:8000/health)
- Streamlit: [http://localhost:8501](http://localhost:8501) (`API_BASE_URL` apunta a `http://app:8000` para el healthcheck del sidebar).

El Schema Agent se ejecuta **en el proceso de Streamlit** (misma imagen); el boton *Verificar API* solo comprueba que FastAPI responda.

**Nota Docker:** el `Dockerfile` define un HEALTHCHECK contra el puerto **8000** (uvicorn). El servicio `streamlit` en `docker-compose.yml` **sobrescribe** el healthcheck para el puerto **8501**; si no, el contenedor de la UI quedaba *unhealthy* aunque Streamlit estuviera bien.

## Environment Configuration

1. Copy environment variables:
   - `cp .env.example .env`
2. Adjust `DATABASE_URL` if needed.

Key variables:

- DB: `DATABASE_URL`, `DB_CONNECT_TIMEOUT`
- LLM: `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`
- App: `APP_HOST`, `APP_PORT`, `APP_ENV`, `APP_LOG_LEVEL`
- Flags: `ENABLE_SCHEMA_AGENT`, `ENABLE_QUERY_AGENT`
- Schema docs: `SCHEMA_DOCS_PATH`, `SCHEMA_AGENT_MAX_ITERATIONS`

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
pytest tests/test_u_query_agent.py
pytest tests/test_in_query_agent.py
```