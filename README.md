# langgraph-nl2sql-system

Multi-agent **Natural Language → SQL** system over PostgreSQL, built with **LangGraph**. Fulfills the individual assessment requirements (`CONSIGNA.md`): two specialized agents, Human-in-the-Loop (HITL), persistent + short-term memory, MCP tools, read-only execution, and a reproducible demo on the **DVD Rental** dataset.

- **Mandatory dataset:** PostgreSQL DVD Rental (pagila).
- **Framework:** LangGraph (graph with explicit nodes/edges, typed state, conditional routing, and `interrupt` for HITL).
- **LLM:** any endpoint compatible with OpenAI Chat Completions (via `LLM_BASE_URL` + `LLM_API_KEY`).
- **UI:** Streamlit with two independent tabs (Schema Agent / Query Agent).
- **Observability:** JSON-friendly logs + optional LangSmith.

---

## Table of Contents

1. [Two-agent architecture](#1-two-agent-architecture)
2. [LangGraph Mermaid diagrams](#2-langgraph-mermaid-diagrams)
3. [Agent patterns applied](#3-agent-patterns-applied)
4. [MCP tools and their role](#4-mcp-tools-and-their-role)
5. [Memory design](#5-memory-design-persistent--short-term)
6. [Setup and run](#6-setup-and-run)
7. [Loading the DVD Rental dataset](#7-loading-the-dvd-rental-dataset)
8. [UI and programmatic usage](#8-ui-and-programmatic-usage)
9. [End-to-end demo](#9-reproducible-end-to-end-demo)
10. [Tests and evaluation](#10-tests-and-evaluation)
11. [Project structure](#11-project-structure)
12. [Environment variables](#12-environment-variables)
13. [Troubleshooting](#13-troubleshooting)
14. [Deliverables checklist](#14-deliverables-checklist-from-consigna)

---

## 1. Two-agent architecture

The assignment requires **exactly two specialized agents** with clearly separated responsibilities and prompts. They are also separated in the UI (one tab each) and communicate only through **persisted artifacts** (`data/schema_docs.json`), not via runtime hand-off. This keeps the coupling low and makes each agent independently auditable and testable.

| Agent          | Responsibility                                                                                          | Graph                                | Prompts                                         |
| -------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------ | ----------------------------------------------- |
| `SchemaAgent`  | Schema introspection, NL description generation per table/column, **HITL**, persistence.                | `graph/schema_graph.py`              | `prompts/schema_agent.py`                       |
| `QueryAgent`   | NL→SQL with plan + critic + read-only execution + explanation + follow-up support (short-term memory). | `graph/query_graph.py`               | `prompts/query_agent.py`, `prompts/security.py` |

Integration flow (no runtime hand-off):

```
SchemaAgent ──(approves and persists)──► data/schema_docs.json ──(read in prepare)──► QueryAgent
```

If there is no approved schema, `QueryAgent` returns `status="blocked_missing_schema"` asking the user to run the Schema Agent first. This makes the dependency **explicit**, not implicit.

---

## 2. LangGraph Mermaid diagrams

### 2.1 Schema Agent (HITL via `interrupt`)

Flow: `agent ↔ tools` (ReAct over `mcp_schema_inspect`) → `format_draft` → `human_gate` (pauses with `interrupt`) → `persist_approved`. Requires a `BaseCheckpointSaver` (default `MemorySaver`) so it can pause/resume.

```mermaid
flowchart TD
    START([START])
    A["agent<br/>(LLM + tool-calling)"]
    T["tools<br/>mcp_schema_inspect"]
    FD["format_draft<br/>normalize JSON draft"]
    HG{{"human_gate<br/>interrupt(draft_document)"}}
    PA["persist_approved<br/>SchemaDocsStore.append"]
    END_NODE([END])

    START --> A
    A -->|tool_calls + iter < max| T
    A -->|no tool_calls| FD
    T --> A
    FD --> HG
    HG -- approve --> PA
    HG -- edit (edited_document) --> PA
    HG -- reject --> PA
    PA --> END_NODE

    classDef human fill:#ffd54f,stroke:#f57f17,color:#000,font-weight:bold;
    class HG human;
    classDef tool fill:#81d4fa,stroke:#01579b,color:#000;
    class T tool;
    classDef persist fill:#a5d6a7,stroke:#1b5e20,color:#000;
    class PA persist;
```

- LangGraph's checkpointer stores state between `interrupt()` and `Command(resume=...)`, keyed by `thread_id = session_id`.
- `human_feedback` accepts `{"action": "approve" | "edit" | "reject", "edited_document": {...}}`.
- `persist_approved` does an append-only write to `data/schema_docs.json` with an incremented `version` and `approved_at`.

### 2.2 Query Agent (Planner / Critic / Executor with retries)

Flow: `prepare → planner → critic → execute → finish → prefs_finalize → END`, with controlled **retry loops** (planner ↔ critic and planner ↔ execute) up to the maximum budget.

```mermaid
flowchart TD
    START([START])
    P["prepare<br/>load schema + prefs + session"]
    PL["planner<br/>intent + SQL + MVS + plan_feedback"]
    CR["critic<br/>validate SQL (read-only + semantics)"]
    EX["execute<br/>mcp_sql_query (LIMIT/sample)"]
    FIN["finish<br/>explanation + save session"]
    PF["prefs_finalize<br/>detect/persist preferences"]
    END_NODE([END])

    START --> P
    P -->|schema ok| PL
    P -->|blocked_missing_schema| FIN
    PL -->|candidates ok| CR
    PL -->|needs_clarification / no tables| FIN
    CR -->|approved| EX
    CR -->|fixable + retries < max| PL
    CR -->|unfixable / no retries| FIN
    EX -->|repairable runtime_error| PL
    EX -->|ok or final error| FIN
    FIN --> PF
    PF --> END_NODE

    classDef plan fill:#ffcc80,stroke:#e65100,color:#000;
    classDef exec fill:#81d4fa,stroke:#01579b,color:#000;
    classDef mem fill:#ce93d8,stroke:#4a148c,color:#000;
    class PL,CR plan;
    class EX exec;
    class P,FIN,PF mem;
```

- `prepare` reads: `SchemaDocsStore` (approved), `PersistentStore` (preferences), `SessionStore` (short-term memory: `last_sql`, `recent_filters`, `working_messages`).
- `planner` uses schema + preferences + session to produce `logical_plan`, `candidate_tables`, `minimum_viable_schema`, and the final SQL.
- `critic` applies SQL guardrails (only `SELECT`, no `DROP/DELETE/UPDATE/ALTER`) and coherence checks against the approved schema; can trigger a retry back to `planner` with `plan_feedback_source="critic"`.
- `execute` runs read-only via `mcp_sql_query`; a repairable runtime error triggers another retry with `plan_feedback_source="execution"`.
- `finish` composes explanation + `sample` + final SQL and persists short-term memory.
- `prefs_finalize` detects preference commands (e.g. "always answer me in English") and persists them, off the latency critical path.

### 2.3 Combined view (integration via artifacts)

```mermaid
flowchart LR
    subgraph SchemaAgent [Schema Agent · HITL]
      SA_START([START]) --> SA_AGENT["agent"] --> SA_TOOLS["tools<br/>mcp_schema_inspect"]
      SA_TOOLS --> SA_AGENT
      SA_AGENT --> SA_FMT["format_draft"] --> SA_HG{{"human_gate<br/>interrupt"}}
      SA_HG --> SA_PERSIST["persist_approved"] --> SA_END([END])
    end

    subgraph Storage [Memory + Artifacts]
      DOCS[(schema_docs.json<br/>versioned)]
      PREFS[(user_preferences<br/>Postgres or JSON)]
      SESS[(session_memory.json<br/>TTL)]
    end

    subgraph QueryAgent [Query Agent · Planner/Critic/Executor]
      Q_START([START]) --> Q_PREP["prepare"] --> Q_PLAN["planner"] --> Q_CRIT["critic"] --> Q_EXEC["execute<br/>mcp_sql_query"] --> Q_FIN["finish"] --> Q_PF["prefs_finalize"] --> Q_END([END])
      Q_CRIT -.retry.-> Q_PLAN
      Q_EXEC -.retry.-> Q_PLAN
    end

    SA_PERSIST --> DOCS
    DOCS --> Q_PREP
    PREFS <--> Q_PREP
    PREFS <--- Q_PF
    SESS <--> Q_PREP
    SESS <--- Q_FIN
```

---

## 3. Agent patterns applied

Covers item 5 of the assignment (at least three patterns):

1. **Planner / Executor** (`planner` ↔ `execute`): separates reasoning (intent + SQL + rationale) from action (read-only execution).
2. **Critic / Validator** (`critic`): second validation pass before executing SQL. Combines syntactic guardrails (`tools/sql_guard.py`) and semantic coherence against the approved schema. Can force a retry back to `planner`.
3. **Human-in-the-Loop** (`human_gate` in the Schema Agent): the graph literally pauses with `interrupt()` and waits for `Command(resume=...)` carrying human feedback before persisting anything.
4. **Router** (`graph/query_edges.py`, `graph/schema_edges.py`): `add_conditional_edges` picks the next transition based on state, not on the last message.
5. **Reflection / Retries**: `plan_feedback` + `plan_feedback_source` feed the planner on the next attempt, bounded by a maximum budget to avoid infinite loops.
6. **Guardrails**: `tools/sql_guard.py` + `prompts/security.py` block DDL/DML even if the LLM emits them; optionally, a **read-only Postgres role** (`scripts/sql/readonly_guardrail.sql`) enforces this at the DB level.
7. **ReAct** (Schema Agent): the `agent` node alternates tool-calls (`mcp_schema_inspect`) with reasoning until the draft is complete.

---

## 4. MCP tools and their role

All tools are invoked from the graph and leave traces in logs (`mcp_tool_call` / `mcp_tool_response` with a unique `call_id`).

| Tool                     | File                         | Input                                                    | Usage                                                                                      |
| ------------------------ | ---------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `mcp_schema_inspect`     | `tools/mcp_schema_tool.py`   | `table_names?`, `include_samples?`, `sample_rows (3..5)` | Schema Agent · metadata for tables, columns, PK/FK, and samples to write NL descriptions. |
| `mcp_sql_query`          | `tools/mcp_sql_tool.py`      | `sql (SELECT)`                                           | Query Agent · read-only execution; blocks DDL/DML via `validate_read_only_sql`.            |

Execution modes (controlled by `MCP_TOOLS_MODE`):

- `local`: the tool imports `tools.service` and opens a direct connection (useful in dev/tests).
- `http`: the tool POSTs to the compose `tools` service (`http://tools:8010`). That service isolates credentials (e.g. the read-only role) from the agent process.

`MCP_TOOLS_MODE=http` is mandatory when you run from the host against the compose stack, because the read-only credentials live only inside the `tools` container.

---

## 5. Memory design (persistent + short-term)

### 5.1 Persistent memory — `memory/persistent_store.py`

**What it stores and why:** user preferences that must survive across sessions to personalize prompts and explanations.

- Fields: `language`, `format` (`table` | `json`), `date_preference`, `strictness` (more/less strict guardrails).
- Backends: `postgres` (`user_preferences` table, UPSERT by `user_id`) or `json` (`USER_PREFERENCES_PATH` file, useful for tests).
- Selector: `PREFERENCES_STORE_BACKEND=postgres|json`.
- Read: in `prepare` (injected into the `planner` prompt).
- Write: in `prefs_finalize`, which detects preference commands and UPSERTs them **at the end of the turn** (off the latency critical path).

### 5.2 Short-term memory — `memory/session_store.py` + `memory/working.py`

**What it stores and why:** conversational continuity (follow-ups) and reasoning context within the same `session_id`. Pattern inspired by CoALA / DEMO02-memory.

- JSON file per `session_id` (`SESSION_MEMORY_PATH`) with TTL (`SESSION_MEMORY_TTL_SECONDS`).
- Structured fields: `last_question`, `last_sql`, `assumptions`, `clarifications`, `recent_filters`.
- `WorkingMemory` (`memory/working.py`): `role/content` buffer with a token cap (`WORKING_SESSION_TOKEN_LIMIT`) that feeds the planner with the latest N turns.
- Read: in `prepare` (enables pronoun resolution like "those" in follow-ups).
- Write: in `finish`, after the result is confirmed.

### 5.3 Schema documentation memory — `memory/schema_docs_store.py`

Append-only JSON (`SCHEMA_DOCS_PATH`) holding `version`, `approved_at`, and the full approved descriptions document. Nothing is deleted when edited: each approval creates a new version. The `QueryAgent` always reads the latest approved version.

### 5.4 Memory traces — `memory/trace.py`

`trace_log("WORKING" | "EPISODIC" | ...)` emits structured logs for memory-flow observability (what each node read/wrote).

---

## 6. Setup and run

### 6.1 Prerequisites

- Python 3.11+.
- Docker Desktop with `docker compose`.
- `LLM_API_KEY` for an OpenAI-compatible Chat Completions endpoint.
- Optional: LangSmith account (`LANGCHAIN_API_KEY`) for tracing.

### 6.2 Environment variables

```bash
cp .env.example .env
```

Edit at least `DATABASE_URL`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`. Full reference in [section 12](#12-environment-variables).

### 6.3 Option A — Full stack with Docker Compose (recommended)

Brings up Postgres (DVD rental) + Postgres (preferences) + HTTP tools + FastAPI + Streamlit:

```bash
docker compose up --build
```

- FastAPI: <http://localhost:8000/health>
- HTTP MCP tools: <http://localhost:8010/health>
- Streamlit UI: <http://localhost:8501>
- Postgres DVD rental: `localhost:5433`
- Postgres preferences: `localhost:5434`

**Postgres + UI only** (no FastAPI; the *Verify API* button will fail until you also start `app`):

```bash
docker compose up postgres postgres_prefs tools streamlit --build
```

### 6.4 Option B — Local run without Docker

```bash
pip install -e ".[dev,ui]"
uvicorn main:app --reload --host 0.0.0.0 --port 8000
# in another terminal
streamlit run streamlit_app/app.py --server.port 8501
```

You need a local Postgres with DVD Rental loaded (see [section 7](#7-loading-the-dvd-rental-dataset)) and `MCP_TOOLS_MODE=local`.

---

## 7. Loading the DVD Rental dataset

The assignment mandates the official **DVD Rental (pagila)** dataset for development, evaluation, and demo.

### 7.1 Automatic bootstrap (compose)

`docker-compose.yml` mounts `./data/dvdrental.tar` and runs `scripts/sql/001_restore_dvdrental.sh` as an init script the first time the Postgres volume is created. If you already ran without the `.tar` and want to force a restore, destroy the volume:

```bash
docker compose down -v
docker compose up --build
```

### 7.2 Manual bootstrap

```bash
docker compose up -d postgres
docker exec -i langgraph-nl2sql-postgres dropdb -U postgres --if-exists dvdrental
docker exec -i langgraph-nl2sql-postgres createdb -U postgres dvdrental
docker cp data/dvdrental.tar langgraph-nl2sql-postgres:/tmp/dvdrental.tar
docker exec -i langgraph-nl2sql-postgres pg_restore -U postgres -d dvdrental /tmp/dvdrental.tar
docker exec -i langgraph-nl2sql-postgres psql -U postgres -d dvdrental -c "\dt public.film public.actor public.rental"
```

Official dump download: <https://neon.com/postgresql/postgresql-getting-started/postgresql-sample-database>. Drop it at `data/dvdrental.tar`.

### 7.3 Extra DB-level guardrail (optional but recommended)

Create an `nl2sql_reader` role with `SELECT`-only privileges:

```bash
docker exec -i langgraph-nl2sql-postgres psql -U postgres -d dvdrental \
  -f /docker-entrypoint-initdb.d/002_readonly_guardrail.sql
```

Then point `DATABASE_URL` at the read-only user. This way, even if app-level guardrails fail, the DB itself rejects any write. Details in `scripts/bootstrap_dvdrental.md`.

---

## 8. UI and programmatic usage

### 8.1 Streamlit (two tabs)

1. Open <http://localhost:8501>.
2. **Schema Agent tab:** click `Document schema` → the graph runs until `human_gate`, renders the editable JSON draft → choose `approve | edit | reject` → result is persisted to `data/schema_docs.json`.
3. **Query Agent tab:** ask questions in NL. Reusing the same `session_id` activates short-term memory (follow-ups).

### 8.2 Programmatic API

```python
from agents.schema_agent import SchemaAgentRunner
from agents.query_agent import QueryAgent

runner = SchemaAgentRunner()
state = runner.start(session_id="demo-schema-001", reset_schema=True)
draft = state["__interrupt__"][0].value["draft_document"]
final = runner.resume(
    session_id="demo-schema-001",
    human_feedback={"action": "approve"},
)

agent = QueryAgent()
resp = agent.run(
    "Total revenue by film category",
    session_id="demo-query-001",
    user_id="demo-user",
)
print(resp["status"])        # ok | needs_clarification | blocked_missing_schema
print(resp["sql_final"])
print(resp["sample"])
print(resp["explanation"])
```

---

## 9. Reproducible end-to-end demo

`scripts/demo.py` runs the 4 mandatory scenarios in a single pass:

- **STEP 0:** healthcheck against `app:8000` and `tools:8010`.
- **STEP 1:** Schema Agent with `reset_schema=True`, waits on `interrupt`, edits `customer.activebool` and `film.rating`, resumes with `action="edit"`, and persists a new version.
- **STEP 2:** three NL queries (simple / business rule / canonical join) showing each node and the final SQL.
- **STEP 3:** follow-up "and now filter only the ones in the Action category" using the previous `session_id` (short-term memory).
- **STEP 4:** final summary and deliverables checklist.

From the host (PowerShell):

```powershell
$env:MCP_TOOLS_MODE="http"
$env:PYTHONIOENCODING="utf-8"
python scripts/demo.py
```

From the host (bash):

```bash
MCP_TOOLS_MODE=http PYTHONIOENCODING=utf-8 python scripts/demo.py
```

From inside the `app` container:

```bash
docker exec -it langgraph-nl2sql-app python scripts/demo.py
```

Optional env vars: `DEMO_API_URL`, `DEMO_TOOLS_URL`, `DEMO_LOG_LEVEL`.

Step-by-step narrated walkthrough (expected SQL, samples, alternative UI flow): **`docs/demo-script.md`**.

---

## 10. Tests and evaluation

### 10.1 Unit / integration tests

```bash
pytest tests/test_u_settings.py
pytest tests/test_in_db_connectivity.py
pytest tests/test_f_health.py
pytest tests/test_u_query_agent.py
pytest tests/test_in_query_agent.py
```

### 10.2 Deterministic Query Agent eval suite

Boolean scoring, no LLM-as-judge, so baseline vs. prompt-change comparisons are reproducible:

```bash
python -m evals.run                              # all cases
python -m evals.run --tags business_rule         # single tag
python -m evals.run --case-id canonical_revenue_by_category
python -m evals.run --save runs/current.json
python -m evals.compare runs/baseline.json runs/current.json
```

Full list of checks and tags in `evals/README.md`.

---

## 11. Project structure

```
langgraph-nl2sql-system/
├── agents/
│   ├── schema_agent.py          # SchemaAgentRunner (start/resume/stream)
│   └── query_agent.py           # QueryAgent.run() end-to-end
├── graph/
│   ├── schema_graph.py          # Schema Agent StateGraph + MemorySaver
│   ├── schema_nodes.py          # agent / tools / format_draft / human_gate / persist_approved
│   ├── schema_edges.py          # Schema Agent routing
│   ├── schema_state.py          # SchemaAgentState (TypedDict)
│   ├── schema_format.py         # JSON draft normalization
│   ├── query_graph.py           # Query Agent StateGraph
│   ├── query_nodes.py           # prepare / planner / critic / execute / finish / prefs_finalize
│   ├── query_edges.py           # routing + retry loops
│   └── query_state.py           # QueryAgentState (TypedDict)
├── memory/
│   ├── persistent_store.py      # preferences (postgres | json)
│   ├── session_store.py         # session memory + TTL
│   ├── working.py               # message buffer by tokens
│   ├── schema_docs_store.py     # append-only schema docs
│   ├── trace.py                 # trace_log for observability
│   └── memoria_diseno.txt       # design notes
├── tools/
│   ├── mcp_schema_tool.py       # MCP: mcp_schema_inspect
│   ├── mcp_sql_tool.py          # MCP: mcp_sql_query
│   ├── sql_guard.py             # validate_read_only_sql (syntactic guardrail)
│   ├── service.py               # local implementation
│   └── http_client.py           # HTTP client for tools service
├── prompts/
│   ├── schema_agent.py          # Schema Agent system prompt + few-shots
│   ├── query_agent.py           # Planner / Critic / Finalize prompts
│   └── security.py              # read-only security rules
├── observability/
│   ├── langsmith_setup.py       # opt-in LangSmith
│   └── mcp_tracing.py           # MCP preload + tracing
├── streamlit_app/               # UI (Schema / Query tabs)
├── evals/                       # Query Agent deterministic suite
├── scripts/
│   ├── demo.py                  # reproducible end-to-end demo
│   ├── bootstrap_dvdrental.md   # official dump steps
│   └── sql/                     # init scripts (restore + readonly guardrail)
├── tests/
├── data/                        # schema_docs.json, session_memory.json, dvdrental.tar
├── docs/
│   └── demo-script.md           # narrated demo walkthrough
├── specs/                       # per-iteration specs
├── main.py                      # FastAPI (GET /health)
├── settings.py                  # Pydantic Settings (loaded from .env)
├── docker-compose.yml           # stack: postgres + postgres_prefs + tools + app + streamlit
├── Dockerfile                   # app/UI image (uvicorn + streamlit)
├── Dockerfile.tools             # HTTP tools image
├── pyproject.toml
├── CONSIGNA.md                  # official assessment brief
└── README.md                    # (this file)
```

---

## 12. Environment variables

Full reference: `.env.example`.

| Variable                       | Default                                                          | Description                                                        |
| ------------------------------ | ---------------------------------------------------------------- | ------------------------------------------------------------------ |
| `APP_ENV`                      | `development`                                                    | Environment.                                                       |
| `APP_LOG_LEVEL`                | `INFO`                                                           | Log level.                                                         |
| `APP_HOST` / `APP_PORT`        | `0.0.0.0` / `8000`                                               | FastAPI bind.                                                      |
| `DATABASE_URL`                 | `postgresql://postgres:postgres@localhost:5432/dvdrental`        | DVD Rental.                                                        |
| `PREFERENCES_DATABASE_URL`     | `postgresql://postgres:postgres@localhost:5434/user_preferences` | Preferences Postgres (isolated).                                   |
| `DB_CONNECT_TIMEOUT`           | `5`                                                              | Connection timeout.                                                |
| `MCP_TOOLS_MODE`               | `local`                                                          | `local` or `http`.                                                 |
| `MCP_TOOLS_BASE_URL`           | `http://localhost:8010`                                          | HTTP tools service.                                                |
| `MCP_TOOLS_TIMEOUT_SECONDS`    | `15`                                                             | Tools timeout.                                                     |
| `LLM_MODEL`                    | `gpt-5-nano`                                                     | LLM model.                                                         |
| `LLM_API_KEY`                  | —                                                                | OpenAI-compatible API key.                                         |
| `LLM_BASE_URL`                 | `https://sa-llmproxy.it.itba.edu.ar`                             | OpenAI-compatible endpoint.                                        |
| `ENABLE_SCHEMA_AGENT`          | `true`                                                           | Feature flag.                                                      |
| `ENABLE_QUERY_AGENT`           | `true`                                                           | Feature flag.                                                      |
| `SCHEMA_DOCS_PATH`             | `data/schema_docs.json`                                          | Approved descriptions file.                                        |
| `SCHEMA_AGENT_MAX_ITERATIONS`  | `15`                                                             | Schema Agent ReAct budget.                                         |
| `USER_PREFERENCES_PATH`        | `data/user_preferences.json`                                     | JSON fallback for preferences.                                     |
| `PREFERENCES_STORE_BACKEND`    | `postgres`                                                       | `postgres` or `json`.                                              |
| `SESSION_MEMORY_PATH`          | `data/session_memory.json`                                       | Session memory file.                                               |
| `SESSION_MEMORY_TTL_SECONDS`   | `3600`                                                           | Session TTL.                                                       |
| `WORKING_SESSION_TOKEN_LIMIT`  | `2000`                                                           | Working-memory buffer cap.                                         |
| `API_BASE_URL`                 | `http://localhost:8000`                                          | Streamlit → FastAPI.                                               |
| `API_TIMEOUT`                  | `15`                                                             | UI healthcheck timeout.                                            |
| `LANGCHAIN_TRACING_V2`         | `false`                                                          | Enable LangSmith.                                                  |
| `LANGCHAIN_API_KEY`            | —                                                                | LangSmith API key.                                                 |
| `LANGCHAIN_PROJECT`            | `langgraph-nl2sql-system`                                        | LangSmith project name.                                            |
| `LANGCHAIN_ENDPOINT`           | `https://api.smith.langchain.com`                                | LangSmith endpoint.                                                |

---

## 13. Troubleshooting

| Symptom                                               | Likely cause                                                         | Fix                                                                                 |
| ----------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `blocked_missing_schema` in Query Agent               | Schema was not approved in the Schema Agent                          | Run Scenario 1 fully (UI or `scripts/demo.py`).                                     |
| `disallowed_statement` in critic                      | LLM produced DDL/DML                                                 | Stops at `critic`, nothing is executed; retry or rephrase.                          |
| `runtime_error` in `execute` on DVD Rental            | Incomplete dataset or missing tables                                 | Redo the bootstrap in section 7 with the official `.tar`.                           |
| `connection failed ... nl2sql_reader` from the host   | Tools in `local` mode trying to connect with the read-only role      | Export `MCP_TOOLS_MODE=http` before running (or use the `tools` container).         |
| `needs_clarification` on a question that shouldn't be | Prompt too vague or short-term memory is empty                       | Rephrase, or seed a prior turn with the same `session_id`.                          |
| Changes in `schema_docs.json` don't reach Query Agent | Cache from a previous run                                            | Redo Scenario 1 or delete `data/schema_docs.json`.                                  |
| `streamlit` container becomes *unhealthy*             | Base image healthcheck hits `:8000`, but Streamlit listens on `:8501`| The compose file overrides the healthcheck to `:8501`; rebuild if the image changed.|

---

## 14. Deliverables checklist (from CONSIGNA)

- [x] Built with **LangGraph** (state + nodes + edges + `interrupt`).
- [x] **Two agents** with separate responsibilities (`SchemaAgent` and `QueryAgent`).
- [x] **Human-in-the-Loop** in the schema documentation flow (`human_gate` with `interrupt`).
- [x] **Persistent memory** for user preferences (`postgres` or `json`).
- [x] **Short-term memory** for conversational continuity (follow-ups).
- [x] **MCP tools** implemented and invoked from the graph (`mcp_schema_inspect`, `mcp_sql_query`).
- [x] NL → SQL with **safe read-only execution** (app guardrails + optional DB role).
- [x] Responses include **SQL + sample + explanation**.
- [x] Demo runs on the official **DVD Rental** dataset.
- [x] **README** with architecture diagram, setup, memory design, MCP tools, and patterns.
- [x] **Demo script** with 1 HITL session + 3 NL queries + 1 follow-up (`docs/demo-script.md` and `scripts/demo.py`).
- [x] **Short report** in `specs/` (design decisions and trade-offs per iteration).
