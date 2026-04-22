# Short Report — Design Choices and Trade-offs

---

## 1. Agent communication via a persisted artifact

The two agents do not hand off state at runtime. The Schema Agent writes an approved document to `data/schema_docs.json`, and the Query Agent reads the latest approved version in its `prepare` node.

- **Good:** each agent is a fully independent LangGraph, with its own state and UI tab. Approvals become an audit trail (append-only, versioned). The dependency is explicit: with no approved schema, the Query Agent returns `blocked_missing_schema` instead of silently failing.
- **Cost:** the Schema Agent must run at least once before any question can be answered; there is no single "end-to-end" run.

## 2. `MemorySaver` as the HITL checkpointer

The Schema Agent pauses at `human_gate` via `interrupt()` and resumes with `Command(resume=...)`. The state between pause and resume is kept by LangGraph's `MemorySaver`, keyed by `thread_id = session_id`.

- **Good:** no extra infrastructure, fastest to set up, survives a Streamlit page reload while the process is alive.
- **Cost:** state is lost if the process restarts. Swapping to a Postgres/SQLite saver in production is a single-line change, but it was left out to keep the demo setup lean.

## 3. Bounded retries with source-tagged feedback

The Query Agent has two retry edges (`critic → planner` and `execute → planner`) capped by a global budget. A `plan_feedback_source` flag tells the planner *why* it is retrying (validation error vs execution error).

- **Good:** small LLM mistakes (wrong column, missing join, runtime error) are fixed without user intervention. Different feedback for different failure types produces better second attempts.
- **Cost:** extra LLM calls raise latency and token cost. The cap avoids infinite loops but also means some edge cases escape to the user as `needs_clarification`.

## 4. Defence in depth for SQL safety

Three stacked layers protect the database:

1. **Prompt rules** (`prompts/security.py`): the LLM is instructed to produce `SELECT` only.
2. **Static check** (`tools/sql_guard.py`): rejects `DROP/DELETE/UPDATE/ALTER` before execution.
3. **Optional DB role** (`scripts/sql/readonly_guardrail.sql`): a Postgres user with read-only privileges.

- **Good:** the three layers cover different failure modes. Prompts alone are unreliable; the static check is strict but only sees text; only the DB role is a hard guarantee.
- **Cost:** more moving parts. The DB role is optional so local setup stays simple, which means the hard guarantee is off by default.

## 5. A dedicated store for approved schema documentation

On top of user preferences (`memory/persistent_store.py`) and session memory (`memory/session_store.py`), a third store (`memory/schema_docs_store.py`) keeps the approved schema as an append-only, versioned file.

| Store | File | Scope | Role |
| --- | --- | --- | --- |
| User preferences | `memory/persistent_store.py` (Postgres or JSON) | across sessions, per user | Personalization (language, format, date style, strictness). |
| Session memory | `memory/session_store.py` + `memory/working.py` | one session, with TTL | Follow-ups. Holds `last_sql`, `recent_filters`, recent turns. |
| Schema docs | `memory/schema_docs_store.py` (append-only) | global, versioned | Each HITL approval creates a new version, so approvals can be audited. |

- **Good:** conversation context, personal settings, and approved schema data have different scopes, lifetimes, and update patterns. Keeping them apart prevents one of them from dragging the others.
- **Cost:** three code paths and three files to maintain instead of one key-value bag.

## 6. MCP tools exposed as a separate HTTP service

`mcp_schema_inspect` and `mcp_sql_query` run as a separate `tools` container and are called over HTTP from the agents. In-process imports are used only in unit tests.

- **Good:** DB credentials (including the read-only role) live inside the tools service and never touch the agent process. Each call is logged with a `call_id`, so the request and its response can be correlated in the trace.
- **Cost:** one extra service to run and one network hop per tool call.

## 7. Structured planner output: intent, minimum viable schema, logical plan

The planner does not return SQL directly. It first emits a JSON object with an ordered set of intermediate fields — `intent`, `minimum_viable_schema`, `candidate_tables`, `candidate_columns`, `logical_plan` — and only then the final `sql`. The field order in the schema is enforced by the prompt so the reasoning is built *before* the query.

- **Good:** forcing the model to commit to the smallest set of relevant tables and a numbered plan in plain prose sharply improves table selection and join correctness on multi-hop queries. The critic can also inspect these intermediate fields, and retries can feed back a specific piece (e.g. wrong table) instead of a blanket "try again". It also makes ambiguous requests easier to catch: if `minimum_viable_schema` ends up empty, the planner is told to ask for clarification instead of guessing.
- **Cost:** the planner prompt is larger (few-shot examples plus the field contract) and its output carries more tokens than raw SQL, which increases latency and per-turn cost.

## 8. Preference updates detected and extracted by an LLM

User preferences (language, output format, date style, strictness, etc.) are not set through a settings UI. They are inferred from natural-language directives embedded in the conversation (e.g. *"from now on reply in English"*, *"always show results as JSON"*). A dedicated node (`prefs_finalize`) runs an LLM-based detector that decides whether the turn contains a directive and, if so, extracts the structured update that gets persisted via `PersistentStore`.

To avoid paying that cost on every turn, the node runs at the **end** of the graph and is gated by a cheap regex pre-filter: plain data questions never trigger the LLM call. For pure-preference commands, the graph still returns a clean "preferences updated" confirmation instead of the planner's attempt at interpreting a non-data question.

- **Good:** users change preferences in the same channel they ask questions — no separate UI, no command syntax to learn. The LLM handles paraphrases, mixed languages, and directives buried inside a data question (*"give me the top 5 films and from now on reply in English"*). Running at the tail of the graph keeps the data answer on the fast path.
- **Cost:** when a directive is actually present, there is an extra LLM call per turn, which adds latency and token cost. A deterministic parser (fixed keywords or slash-commands) would be cheaper and more predictable but would not generalize.

---

## Summary of trade-offs

| Decision | Gained | Gave up |
| --- | --- | --- |
| Agents coupled via persisted artifact | Independent testing, auditing, explicit dependency | A single smooth end-to-end run |
| `MemorySaver` for HITL | Zero extra infra, fast setup | State is lost on process restart |
| Bounded retries with source-tagged feedback | Self-fixes small errors | Extra LLM calls; some cases still need clarification |
| Stacked SQL safety layers | Defence in depth | Optional DB role makes the hard guarantee off-by-default |
| Dedicated store for schema docs | Clean scopes, audit trail on approvals | More code than a single store |
| MCP tools as a separate HTTP service | Isolated credentials, traceable calls | Extra service and network hop |
| Structured planner output (intent + plan + tables) | Better table selection and join correctness | Larger prompt and more output tokens |
| LLM-based preference detection | Natural-language directives, handles paraphrases | Extra LLM call and tokens when a directive is present |

All decisions map directly to the rubric in `CONSIGNA.md` (architecture, schema + HITL, query quality, memory, MCP + patterns, code quality). The full flow is exercised end-to-end by `scripts/demo.py` on the required DVD Rental dataset.
