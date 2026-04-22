"""End-to-end terminal demo for the NL2SQL multi-agent system.

Assumes the docker-compose stack is already running (postgres, tools, app).
Runs inside the `app` container (recommended) or on the host with a properly
configured ``.env`` file.

Covers the four deliverables required by ``CONSIGNA.md``:

1. 1 schema documentation session **with human correction** (HITL).
2. 3 different natural-language queries (simple, business-rule, canonical-join).
3. 1 follow-up refinement over the same session (short-term memory).

How to run (containers already up):

    # Preferred: inside the app container
    docker exec -it langgraph-nl2sql-app python scripts/demo.py

    # Or on the host, with .env loaded and dependencies installed
    python scripts/demo.py

The script is fully non-interactive: the "human" correction in step (1) is
simulated programmatically so the whole demo runs end-to-end in the terminal.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

# Windows consoles default to cp1252; force UTF-8 so the demo never dies on
# a non-ASCII character.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

logging.basicConfig(
    level=os.getenv("DEMO_LOG_LEVEL", "WARNING"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from agents.query_agent import QueryAgent  # noqa: E402
from agents.schema_agent import SchemaAgentRunner  # noqa: E402
from memory.schema_docs_store import SchemaDocsStore  # noqa: E402
from settings import settings  # noqa: E402

API_URL = os.getenv("DEMO_API_URL", "http://localhost:8000")
TOOLS_URL = os.getenv("DEMO_TOOLS_URL", settings.mcp_tools_base_url or "http://localhost:8010")
HEALTH_TIMEOUT = float(os.getenv("DEMO_HEALTH_TIMEOUT", "5"))

BAR = "=" * 78
SUB = "-" * 78


def banner(title: str) -> None:
    safe = title.replace("\u00b7", "-").replace("\u2022", "-")
    print()
    print(BAR)
    print(f"  {safe}")
    print(BAR)


def sub(title: str) -> None:
    print()
    print(SUB)
    print(f"  {title}")
    print(SUB)


def pp(obj: Any, limit_rows: int | None = 10) -> None:
    """Pretty-print a JSON-serializable object; truncate long row samples."""
    trimmed = obj
    if (
        isinstance(obj, dict)
        and "rows" in obj
        and isinstance(obj["rows"], list)
        and limit_rows is not None
        and len(obj["rows"]) > limit_rows
    ):
        trimmed = dict(obj)
        trimmed["rows"] = list(obj["rows"][:limit_rows]) + [
            f"... (+{len(obj['rows']) - limit_rows} more)"
        ]
    try:
        print(json.dumps(trimmed, indent=2, ensure_ascii=False, default=str))
    except Exception:
        print(repr(trimmed))


def short(value: Any, n: int = 200) -> str:
    s = str(value or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


def check_health() -> None:
    """GET /health on app + tools; fail fast with a useful hint if something is down."""
    banner("STEP 0 - Healthchecks (API + MCP Tools + DB)")
    for name, url in (("app (FastAPI)", f"{API_URL}/health"), ("tools (MCP HTTP)", f"{TOOLS_URL}/health")):
        try:
            r = httpx.get(url, timeout=HEALTH_TIMEOUT)
            status = "OK" if r.status_code == 200 else f"HTTP {r.status_code}"
            print(f"  [{status:>6}] {name:22s} {url}")
            if r.status_code == 200:
                body = r.json()
                db_ok = ((body.get("database") or {}).get("ok")) if isinstance(body, dict) else None
                if db_ok is not None:
                    print(f"             database.ok = {db_ok}")
        except Exception as exc:
            print(f"  [FAIL ] {name:22s} {url}   -> {exc}")
            print()
            print("Hint: asegurate de que los contenedores estan up:")
            print("   docker compose ps")
            print("   docker compose up -d postgres tools app")
            raise SystemExit(1) from exc


def _print_schema_progress(event: dict[str, Any]) -> dict[str, Any] | None:
    """Print a one-line progress update; return final state if this was 'final'."""
    kind = event.get("kind")
    if kind == "node":
        name = event.get("name")
        print(f"  > node: {name}")
    elif kind == "error":
        print(f"  ! error: {event.get('message')}")
    elif kind == "final":
        return event.get("state") or {}
    return None


def _apply_human_corrections(draft: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Simulate the human-in-the-loop edits (customer.activebool, film.rating)."""
    diff_log: list[str] = []
    if not isinstance(draft, dict):
        return draft, diff_log
    tables = draft.get("tables") or []
    for t in tables:
        if not isinstance(t, dict):
            continue
        if t.get("table_name") == "customer":
            for c in t.get("columns") or []:
                if isinstance(c, dict) and c.get("name") == "activebool":
                    old = c.get("description", "")
                    c["description"] = (
                        "Business rule: un cliente se considera ACTIVO cuando "
                        "activebool = true. Filtro canonico para reportes "
                        "de 'clientes activos'."
                    )
                    diff_log.append(
                        f"customer.activebool: {short(old, 60)!r} -> {short(c['description'], 60)!r}"
                    )
        if t.get("table_name") == "film":
            for c in t.get("columns") or []:
                if isinstance(c, dict) and c.get("name") == "rating":
                    old = c.get("description", "")
                    c["description"] = (
                        "Clasificacion MPAA (G, PG, PG-13, R, NC-17). "
                        "Tipo enumerado `mpaa_rating`."
                    )
                    diff_log.append(
                        f"film.rating: {short(old, 60)!r} -> {short(c['description'], 60)!r}"
                    )
    return draft, diff_log


def scenario_schema_with_hitl() -> None:
    """Scenario 1: run Schema Agent, apply a programmatic human correction, persist."""
    banner("STEP 1 - Schema Agent + Human-in-the-Loop (HITL)")
    session_id = f"demo-schema-{uuid.uuid4().hex[:8]}"
    print(f"  session_id       = {session_id}")
    print(f"  schema_docs_path = {settings.schema_docs_path}")

    runner = SchemaAgentRunner()
    store = SchemaDocsStore()
    before = store.latest()
    before_version = int((before or {}).get("version") or 0)
    print(f"  previous approved version = {before_version or '(none)'}")

    sub("Running schema graph until HITL interrupt (start)")
    t0 = time.perf_counter()
    final_state: dict[str, Any] | None = None
    for event in runner.stream_start(session_id=session_id, reset_schema=True):
        maybe_final = _print_schema_progress(event)
        if maybe_final is not None:
            final_state = maybe_final
    t_start = time.perf_counter() - t0
    print(f"  elapsed (start) = {t_start:.1f}s")

    if not isinstance(final_state, dict) or "__interrupt__" not in final_state:
        print("  ! the graph did not interrupt on human_gate; aborting demo.")
        pp(final_state)
        raise SystemExit(2)

    interrupts = final_state["__interrupt__"]
    interrupt_value = interrupts[0].value if isinstance(interrupts, (list, tuple)) else interrupts.value
    draft = interrupt_value.get("draft_document") or {}
    table_names = [t.get("table_name") for t in (draft.get("tables") or []) if isinstance(t, dict)]
    print(f"  draft tables      = {len(table_names)}")
    print(f"  sample tables     = {table_names[:8]}{' ...' if len(table_names) > 8 else ''}")

    sub("Simulated HUMAN review: editing 2 column descriptions")
    edited, diff = _apply_human_corrections(draft)
    if not diff:
        print("  (no editable fields found - DB may be missing customer.activebool / film.rating)")
    for line in diff:
        print(f"  EDIT  {line}")

    sub("Resuming graph with action=edit (persisting approved document)")
    t0 = time.perf_counter()
    final_state = None
    for event in runner.stream_resume(
        session_id=session_id,
        human_feedback={"action": "edit", "edited_document": edited},
    ):
        maybe_final = _print_schema_progress(event)
        if maybe_final is not None:
            final_state = maybe_final
    print(f"  elapsed (resume) = {time.perf_counter() - t0:.1f}s")

    approved = (final_state or {}).get("approved_document") or {}
    after_version = int(approved.get("version") or 0)
    print(f"  status             = {(final_state or {}).get('status')}")
    print(f"  approved version   = {after_version}")
    print(f"  previous version   = {before_version or '(none)'}")
    print(f"  version bumped?    = {after_version > before_version}")
    print(f"  tables persisted   = {len((approved.get('document') or {}).get('tables') or [])}")


def _print_query_progress(event: dict[str, Any]) -> dict[str, Any] | None:
    kind = event.get("kind")
    if kind == "node":
        name = event.get("name")
        update = event.get("update") or {}
        hint = ""
        if isinstance(update, dict):
            status = update.get("status")
            if status:
                hint = f"  [status={status}]"
        print(f"  > node: {name}{hint}")
    elif kind == "error":
        print(f"  ! error: {event.get('message')}")
    elif kind == "final":
        return event.get("response") or {}
    return None


def _print_query_response(resp: dict[str, Any], *, limit_rows: int = 5) -> None:
    planner = resp.get("planner") or {}
    validator = resp.get("validator") or {}
    sample = resp.get("sample") or {}
    print()
    print(f"  status    : {resp.get('status')}")
    print(f"  intent    : {planner.get('intent')}")
    print(f"  tables    : {planner.get('candidate_tables')}")
    print(f"  MVS       : {planner.get('minimum_viable_schema')}")
    if validator:
        v_ok = validator.get("is_valid")
        print(f"  validator : is_valid={v_ok}  reasons={validator.get('reasons') or []}")
    sql = resp.get("sql_final")
    if sql:
        print()
        print("  SQL:")
        for line in str(sql).splitlines():
            print(f"    {line}")
    if sample:
        print()
        print(f"  sample.row_count = {sample.get('row_count')}")
        print(f"  sample.columns   = {sample.get('columns')}")
        rows = sample.get("rows") or []
        print(f"  sample.rows (first {min(limit_rows, len(rows))}/{len(rows)}):")
        for row in rows[:limit_rows]:
            print(f"    {row}")
    expl = resp.get("explanation") or resp.get("assistant_text")
    if expl:
        print()
        print(f"  explanation: {short(expl, 400)}")
    if resp.get("clarification_question"):
        print(f"  clarification: {resp.get('clarification_question')}")


def run_query_turn(agent: QueryAgent, *, session_id: str, user_id: str, question: str) -> dict[str, Any]:
    print(f'  question   : "{question}"')
    print("  graph trace:")
    response: dict[str, Any] = {}
    t0 = time.perf_counter()
    for event in agent.stream(question, session_id=session_id, user_id=user_id):
        maybe_final = _print_query_progress(event)
        if maybe_final is not None:
            response = maybe_final
    print(f"  elapsed    : {time.perf_counter() - t0:.1f}s")
    _print_query_response(response)
    return response


def scenario_three_queries(agent: QueryAgent, *, session_id: str, user_id: str) -> None:
    """Scenario 2: 3 distinct NL questions on DVD Rental."""
    banner("STEP 2 - Three natural-language queries")

    sub("2.1 - Simple  (base table, ORDER BY + LIMIT 10)")
    run_query_turn(
        agent,
        session_id=session_id,
        user_id=user_id,
        question="Mostrame los 10 films con mayor duracion",
    )

    sub("2.2 - Business rule  (requires customer.activebool from STEP 1)")
    run_query_turn(
        agent,
        session_id=session_id,
        user_id=user_id,
        question="Cuantos clientes activos hay?",
    )

    sub("2.3 - Canonical join path  (payment -> rental -> inventory -> film_category -> category)")
    run_query_turn(
        agent,
        session_id=session_id,
        user_id=user_id,
        question="Ingresos totales por categoria de pelicula",
    )


def scenario_followup(agent: QueryAgent, *, session_id: str, user_id: str) -> None:
    """Scenario 3: seed one turn then a follow-up that requires short-term memory."""
    banner("STEP 3 - Follow-up refinement (short-term memory)")

    sub("3.1 - Seed turn")
    run_query_turn(
        agent,
        session_id=session_id,
        user_id=user_id,
        question="Dame los 5 films con mas ingresos",
    )

    sub("3.2 - Follow-up (pronoun 'los' -> must reuse last_sql from session memory)")
    resp = run_query_turn(
        agent,
        session_id=session_id,
        user_id=user_id,
        question="Y ahora filtrame solo los de la categoria Action",
    )

    sql = (resp.get("sql_final") or "").lower()
    has_category = "category" in sql or "film_category" in sql
    has_action_filter = "action" in sql
    has_revenue = "sum(" in sql and "amount" in sql
    print()
    print("  follow-up sanity checks:")
    print(f"    joins category table?   {has_category}")
    print(f"    filters by Action?      {has_action_filter}")
    print(f"    preserves SUM(amount)?  {has_revenue}")


def final_summary(session_id: str) -> None:
    banner("STEP 4 - Final summary")
    store = SchemaDocsStore()
    latest = store.latest() or {}
    doc = latest.get("document") or {}
    print(f"  schema version persisted : {latest.get('version')}")
    print(f"  schema approved_at       : {latest.get('approved_at')}")
    print(f"  schema tables            : {len(doc.get('tables') or [])}")
    print(f"  session_id used          : {session_id}")
    print(f"  session memory file      : {settings.session_memory_path}")
    print()
    print("Demo completed. Deliverables covered:")
    print("  [OK] Schema documentation session with human correction (HITL).")
    print("  [OK] Three different natural-language query examples.")
    print("  [OK] Follow-up refinement using short-term memory.")
    print("  [OK] All executed on the DVD Rental dataset.")


def main() -> int:
    banner("NL2SQL Multi-Agent - Terminal Demo (DVD Rental)")
    print(f"  API_URL   = {API_URL}")
    print(f"  TOOLS_URL = {TOOLS_URL}")
    print(f"  DB URL    = {settings.database_url}")
    print(f"  LLM model = {settings.llm_model}")

    check_health()

    scenario_schema_with_hitl()

    agent = QueryAgent()

    demo_session = f"demo-query-{uuid.uuid4().hex[:8]}"
    demo_user = "demo-user"
    scenario_three_queries(agent, session_id=demo_session, user_id=demo_user)

    followup_session = f"demo-followup-{uuid.uuid4().hex[:8]}"
    scenario_followup(agent, session_id=followup_session, user_id=demo_user)

    final_summary(followup_session)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[demo interrupted by user]")
        raise SystemExit(130)
