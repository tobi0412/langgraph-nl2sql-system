"""LangGraph nodes for Query Agent.

Memory policy:
- prepare: reads PersistentStore (preferences by user_id) and SessionStore (session context),
  writes memory_context_text + persistent_prefs.
- planner / critic: read-only state usage; planner uses enriched memory context for NL2SQL.
- execute: read-only state.
- finalize: writes SessionStore (latest question, SQL, filters, assumptions, clarifications).

Repair loop:
- planner -> critic. If critic rejects on a fixable correctness/syntax issue
  (not user-ambiguity) the graph routes back to planner with `plan_feedback`
  source="critic".
- critic -> execute. If the SQL raises at runtime, the execute node sets
  `plan_feedback` with source="execution" and the graph routes back to
  planner to fix it.
- Retries are capped by ``MAX_PLAN_RETRIES``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from graph.query_state import QueryAgentState
from llm.chat_model import get_chat_model
from memory.persistent_store import PersistentStore
from memory.schema_docs_store import SchemaDocsStore
from memory.session_store import SessionSnapshot, SessionStore
from prompts.query_agent import (
    QUERY_CRITIC_SYSTEM_PROMPT,
    QUERY_PLANNER_SQL_SYSTEM_PROMPT,
    QUERY_PREFERENCES_UPDATE_SYSTEM_PROMPT,
)
from settings import settings
from tools.mcp_sql_tool import MCPSQLQueryTool
from tools.sql_guard import validate_read_only_sql

MAX_PLAN_RETRIES = 3


def _build_pending_clarification_text(snap: SessionSnapshot, current_question: str) -> str:
    """Render an explicit "pending clarification" block when the previous turn
    ended asking the user for clarification.

    The planner uses this block (via its prompt rule) to commit to SQL with
    best-guess assumptions instead of re-asking the same question.
    """
    if snap.last_status != "needs_clarification":
        return ""
    if not snap.clarifications:
        return ""
    last_clarification = snap.clarifications[-1].strip()
    if not last_clarification:
        return ""
    original_q = (snap.last_question or "").strip()
    parts = [
        "Pending clarification cycle (the previous assistant turn asked for clarification):",
        f"- Previous assistant clarification question: \"{last_clarification}\"",
    ]
    if original_q:
        parts.append(f"- Original question being resolved: \"{original_q}\"")
    parts.append(f"- User's answer this turn: \"{current_question.strip()}\"")
    return "\n".join(parts)


def _build_memory_context_text(prefs: dict[str, str], snap: SessionSnapshot) -> str:
    lines: list[str] = [
        "Persistent user preferences (internal metadata): "
        f"language={prefs.get('language', 'es')}; "
        f"response_format={prefs.get('format', 'markdown')}; "
        f"date_format={prefs.get('date_preference', 'iso')}; "
        f"strictness={prefs.get('strictness', 'normal')}."
    ]
    if snap.working_messages:
        dial: list[str] = []
        for m in snap.working_messages[-5:]:
            role = str(m.get("role", "?"))
            content = str(m.get("content", ""))[:420]
            dial.append(f"{role}: {content}")
        lines.append("Working memory (last 5 messages, token-limited): " + " | ".join(dial))
    if (
        snap.last_question
        or snap.last_sql
        or snap.recent_filters
        or snap.assumptions
        or snap.clarifications
    ):
        lines.append(
            "Session memory (previous turns): "
            f"last_question={snap.last_question or '(none)'}; "
            f"last_sql={snap.last_sql or '(none)'}; "
            f"recent_filters={snap.recent_filters}; "
            f"assumptions={snap.assumptions}; "
            f"previous_clarifications={snap.clarifications[-5:] if snap.clarifications else []}."
        )
    return "\n".join(lines)


def _extract_style_instruction(text: str) -> str | None:
    """Extract persistent response-style instructions from free-form text."""
    raw = (text or "").strip()
    if not raw:
        return None
    # Capture intent like "desde ahora...", "a partir de ahora...", "siempre..."
    # and keep the whole instruction so it can be sent to planner/generator.
    match = re.search(
        r"((?:desde|a partir de)\s+ahora[\s\S]{0,220}|siempre[\s\S]{0,220})",
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    # Fallback: explicit style hints in same turn.
    if re.search(r"\b(?:responde|respondeme|contesta|prefijo|tono|formato)\b", raw, re.IGNORECASE):
        return raw[:220].strip()
    if re.search(r"\b(?:respond|answer|prefix|tone|style|format)\b", raw, re.IGNORECASE):
        return raw[:220].strip()
    return None


def _table_names(schema_context: dict[str, dict[str, Any]]) -> list[str]:
    return [name for name in schema_context.keys() if isinstance(name, str) and name.strip()]


def _table_columns(schema_context: dict[str, dict[str, Any]], table_name: str) -> list[str]:
    table_meta = schema_context.get(table_name) or {}
    if isinstance(table_meta, list):
        return [str(col).strip() for col in table_meta if isinstance(col, str) and str(col).strip()]
    if not isinstance(table_meta, dict):
        return []
    cols = table_meta.get("columns")
    if not isinstance(cols, list):
        return []
    out: list[str] = []
    for col in cols:
        if not isinstance(col, dict):
            continue
        name = col.get("name")
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def _resolve_response_style_instruction(question: str, snap: SessionSnapshot) -> str | None:
    """Resolve style instruction from current turn or session memory."""
    current_style = _extract_style_instruction(question)
    if current_style:
        return current_style
    for msg in reversed(snap.working_messages):
        if str(msg.get("role")) != "user":
            continue
        content = str(msg.get("content", ""))
        found_style = _extract_style_instruction(content)
        if found_style:
            return found_style
    return None


def _assistant_turn_summary(state: QueryAgentState) -> str:
    """Turn summary written into working memory.

    Prefers the LLM's ``assistant_text`` over the (now mostly empty)
    ``explanation`` field so the session memory keeps a user-facing,
    language-appropriate recap of the turn instead of English debug strings.
    """
    status = str(state.get("status") or "")
    assistant_text = str(state.get("assistant_text") or "").strip()
    if status == "ok":
        sql = str(state.get("sql_candidate") or "")[:400]
        text = (assistant_text or str(state.get("explanation") or ""))[:400]
        return f"SQL: {sql} | {text}" if text else f"SQL: {sql}"
    if status == "needs_clarification":
        return (
            str(state.get("clarification_question") or "")
            or assistant_text
            or f"[status={status}]"
        )[:500]
    if status == "blocked_missing_schema":
        return "Blocked: no approved schema descriptions are available."
    if status == "execution_error":
        return (str(state.get("explanation") or "") or assistant_text or f"[status={status}]")[:500]
    return (assistant_text or str(state.get("explanation") or "") or f"[status={status}]")[:500]


def prepare_query_node(state: QueryAgentState) -> dict[str, Any]:
    """Load approved schema docs and block if unavailable."""
    user_id = str(state.get("user_id") or "default")
    session_id = str(state.get("session_id") or "default")
    store = SchemaDocsStore()
    latest = store.latest()
    schema_context = store.extract_query_schema_context(latest)
    question_text = str(state.get("question") or "")
    if not schema_context:
        snap = SessionStore().get_snapshot(session_id)
        response_style_instruction = _resolve_response_style_instruction(question_text, snap)
        return {
            "schema_context": {},
            "status": "blocked_missing_schema",
            "sql_candidate": None,
            "sample": None,
            "explanation": "There are no approved schema descriptions available to answer NL2SQL queries.",
            "limitations": [
                "The Query Agent cannot run schema inspection.",
                "First generate/approve schema descriptions in the Schema Agent tab.",
            ],
            "clarification_question": (
                "After finishing in Schema Agent, return to this tab and retry your query."
            ),
            "validator": {},
            "candidate_tables": [],
            "user_id": user_id,
            "persistent_prefs": PersistentStore().get_preferences(user_id),
            "memory_context_text": "",
            "response_style_instruction": response_style_instruction,
            "pending_clarification_text": "",
        }
    persistent = PersistentStore().get_preferences(user_id)
    snap = SessionStore().get_snapshot(session_id)
    memory_context_text = _build_memory_context_text(persistent, snap)
    pending_clarification_text = _build_pending_clarification_text(snap, question_text)
    response_style_instruction = _resolve_response_style_instruction(question_text, snap)
    return {
        "schema_context": schema_context,
        "user_id": user_id,
        "persistent_prefs": persistent,
        "memory_context_text": memory_context_text,
        "response_style_instruction": response_style_instruction,
        "pending_clarification_text": pending_clarification_text,
    }


_ALLOWED_PREF_VALUES: dict[str, set[str]] = {
    "format": {"markdown", "plain", "json"},
    "date_preference": {"iso", "dd/mm/yyyy", "us"},
    "strictness": {"strict", "normal", "lax"},
}

_LANGUAGE_ALIAS: dict[str, str] = {
    "english": "en",
    "ingles": "en",
    "inglés": "en",
    "spanish": "es",
    "espanol": "es",
    "español": "es",
    "castellano": "es",
    "portuguese": "pt",
    "portugues": "pt",
    "portugués": "pt",
    "french": "fr",
    "frances": "fr",
    "francés": "fr",
    "italian": "it",
    "italiano": "it",
}


def _sanitize_pref_updates(raw: Any) -> dict[str, str]:
    """Filter LLM output to keys/values we know how to persist."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        val_norm = val.strip().lower()
        if not val_norm:
            continue
        if key == "language":
            mapped = _LANGUAGE_ALIAS.get(val_norm, val_norm)
            if re.fullmatch(r"[a-z]{2,5}", mapped):
                out["language"] = mapped
        elif key in _ALLOWED_PREF_VALUES:
            if val_norm in _ALLOWED_PREF_VALUES[key]:
                out[key] = val_norm
    return out


def _detect_pref_updates(question: str) -> tuple[dict[str, str], bool, str]:
    """Ask the LLM whether the turn contains a preference directive.

    Always runs (no keyword pre-filter): the LLM is the single source of
    truth for deciding whether to update preferences. If the LLM is
    disabled or the call fails, we return ``({}, False, "")`` so the
    turn proceeds unchanged — better to miss a directive than to persist
    a wrong one from a brittle heuristic.
    """
    if not question.strip() or not settings.llm_api_key.strip():
        return {}, False, ""
    try:
        model = get_chat_model(temperature=0)
        prompt = (
            f"{QUERY_PREFERENCES_UPDATE_SYSTEM_PROMPT}\n\nUser question: {question}"
        )
        parsed = _parse_json_object(model.invoke(prompt).content) or {}
    except Exception:  # noqa: BLE001 - prefs detection is optional, never fatal
        return {}, False, ""
    updates = _sanitize_pref_updates(parsed.get("updates") or {})
    pure_command = bool(parsed.get("pure_command"))
    confirmation = str(parsed.get("confirmation") or "").strip()
    return updates, pure_command, confirmation


def _default_prefs_confirmation(updates: dict[str, str]) -> str:
    if not updates:
        return ""
    parts = [f"{k}={v}" for k, v in updates.items()]
    return "Preferences updated: " + ", ".join(parts) + "."


def preferences_update_node(state: QueryAgentState) -> dict[str, Any]:
    """Detect and persist user directives that update persistent preferences.

    Runs at the very start of the query graph (before ``prepare``) so the
    updated prefs are visible to the rest of the pipeline on the same turn.
    The LLM is called on every turn and is the sole authority on whether
    anything needs to change; there is no keyword pre-filter.

    Behavior:
    - LLM returns no updates -> pass-through, the flow continues to
      ``prepare``/``planner`` untouched.
    - LLM returns updates AND ``pure_command=false`` -> persist the updates,
      refresh ``persistent_prefs`` in state, continue to ``prepare``.
    - LLM returns updates AND ``pure_command=true`` -> persist, set
      ``status="preferences_updated"`` and an ``assistant_text`` in the
      user's language, and the router sends the flow straight to ``finish``
      (no SQL generation/execution).
    """
    user_id = str(state.get("user_id") or "default")
    question = str(state.get("question") or "").strip()
    if not question:
        return {}

    updates, pure_command, confirmation = _detect_pref_updates(question)
    if not updates and not pure_command:
        return {}

    if updates:
        try:
            PersistentStore().merge_preferences(user_id, updates)
        except Exception:  # noqa: BLE001 - don't break the turn if the store is down
            pass
    try:
        refreshed = PersistentStore().get_preferences(user_id)
    except Exception:  # noqa: BLE001
        refreshed = dict(state.get("persistent_prefs") or {})

    if pure_command:
        return {
            "persistent_prefs": refreshed,
            "user_id": user_id,
            "status": "preferences_updated",
            "assistant_text": confirmation or _default_prefs_confirmation(updates),
            "sample": None,
            "clarification_question": "",
            "explanation": "",
            "limitations": [],
            "candidate_tables": [],
            "candidate_columns": [],
            "sql_candidate": None,
            "validator": {},
            "plan_feedback": None,
            "plan_feedback_source": None,
        }

    return {"persistent_prefs": refreshed, "user_id": user_id}


def planner_node(state: QueryAgentState) -> dict[str, Any]:
    """Plan intent/tables and draft SQL candidate.

    When re-entered after a critic rejection or runtime SQL error, the state
    carries ``plan_feedback`` + ``plan_feedback_source``; we inject a
    "Previous ... feedback" section so the LLM fixes the same SQL instead of
    starting from scratch. The feedback keys are cleared on the way out so
    the router doesn't treat this attempt as already-failed.
    """
    memory_block = str(state.get("memory_context_text") or "").strip()
    question = str(state.get("question") or "").strip()
    planning_input = (
        f"{memory_block}\n\nCurrent question: {question}" if memory_block else question
    )
    schema_context = state.get("schema_context") or {}
    fallback = _heuristic_plan_and_sql(planning_input, schema_context)
    style_instruction = str(state.get("response_style_instruction") or "").strip()
    style_block = f"\nResponse style instruction: {style_instruction}\n" if style_instruction else "\n"

    plan_feedback = str(state.get("plan_feedback") or "").strip()
    plan_feedback_source = str(state.get("plan_feedback_source") or "").strip()
    previous_sql = str(state.get("sql_candidate") or "").strip()
    feedback_block = ""
    if plan_feedback:
        header = (
            "Previous critic feedback"
            if plan_feedback_source == "critic"
            else "Previous execution error"
        )
        feedback_block = (
            f"\n{header} (the SQL below was rejected or failed; fix it and do NOT repeat the same mistake):\n"
            f"Previous SQL: {previous_sql or '(none)'}\n"
            f"Feedback: {plan_feedback}\n"
        )

    pending_clarification = str(state.get("pending_clarification_text") or "").strip()
    pending_block = f"\n{pending_clarification}\n" if pending_clarification else ""

    retry_count_carry = int(state.get("plan_retry_count") or 0)

    if not settings.llm_api_key.strip():
        update = dict(fallback)
        update["plan_feedback"] = None
        update["plan_feedback_source"] = None
        update["plan_retry_count"] = retry_count_carry
        return update

    model = get_chat_model(temperature=0)
    prompt = (
        f"{QUERY_PLANNER_SQL_SYSTEM_PROMPT}\n\n"
        f"Target database engine: {settings.sql_dialect}. "
        f"Generate SQL that is syntactically valid for this engine.\n\n"
        f"Question (with memory context when present): {planning_input}\n"
        f"{style_block}"
        f"{pending_block}"
        f"{feedback_block}"
        f"Schema context: {json.dumps(schema_context, ensure_ascii=False)}"
    )
    raw = model.invoke(prompt).content
    parsed = _parse_json_object(raw) or {}
    candidate_tables = _string_list(parsed.get("candidate_tables"), fallback["candidate_tables"])
    needs_clarification = bool(parsed.get("needs_clarification", fallback["needs_clarification"]))
    update = {
        "intent": str(parsed.get("intent", fallback["intent"])),
        "candidate_tables": candidate_tables,
        "candidate_columns": _string_list(
            parsed.get("candidate_columns"),
            fallback["candidate_columns"],
        ),
        "needs_clarification": needs_clarification,
        "clarification_question": (
            str(parsed.get("clarification_question"))
            if parsed.get("clarification_question")
            else fallback["clarification_question"]
        ),
        "sql_candidate": _extract_sql(parsed.get("sql")) or _extract_sql(raw) or fallback["sql_candidate"],
        "status": "needs_clarification" if needs_clarification or not candidate_tables else "planned",
        "explanation": "",
        "limitations": [],
        "sample": None,
        "validator": {},
        "assistant_text": str(parsed.get("assistant_text") or "").strip(),
        "plan_feedback": None,
        "plan_feedback_source": None,
        "plan_retry_count": retry_count_carry,
    }
    return update


def critic_node(state: QueryAgentState) -> dict[str, Any]:
    """Critic/validator node before executing SQL."""
    memory_block = str(state.get("memory_context_text") or "").strip()
    question = str(state.get("question") or "").strip()
    schema_context = state.get("schema_context") or {}
    sql_candidate = str(state.get("sql_candidate") or "").strip()
    fallback = _heuristic_validate(question, sql_candidate, state)
    if not settings.llm_api_key.strip():
        return _critic_to_state_update(fallback, state)

    model = get_chat_model(temperature=0)
    mem = (
        f"Memory context (persistent + session):\n{memory_block}\n\n"
        if memory_block
        else ""
    )
    prompt = (
        f"{QUERY_CRITIC_SYSTEM_PROMPT}\n\n"
        f"Target database engine: {settings.sql_dialect}. "
        f"Validate the SQL against this engine's dialect and functions.\n\n"
        f"{mem}"
        f"User question: {question}\n"
        f"Schema context: {json.dumps(schema_context, ensure_ascii=False)}\n"
        f"SQL candidate: {sql_candidate}"
    )
    parsed = _parse_json_object(model.invoke(prompt).content) or {}
    validator = {
        "approved": bool(parsed.get("approved", fallback["approved"])),
        "risk_level": str(parsed.get("risk_level", fallback["risk_level"])),
        "issues": _string_list(parsed.get("issues"), fallback["issues"]),
        "needs_clarification": bool(
            parsed.get("needs_clarification", fallback["needs_clarification"])
        ),
        "clarification_question": (
            str(parsed.get("clarification_question"))
            if parsed.get("clarification_question")
            else fallback["clarification_question"]
        ),
    }
    if validator["needs_clarification"]:
        validator["approved"] = False
    return _critic_to_state_update(validator, state)


def execute_query_node(state: QueryAgentState) -> dict[str, Any]:
    """Execute approved SQL using read-only MCP SQL tool.

    On exception, if the retry budget allows, we record the error as planner
    feedback and let the router send the flow back to the planner. After
    ``MAX_PLAN_RETRIES`` failed attempts we surface the last error to the
    user instead of looping forever.
    """
    sql_candidate = str(state.get("sql_candidate") or "").strip()
    try:
        result = MCPSQLQueryTool()._run(sql_candidate)
    except Exception as exc:  # noqa: BLE001 - we want to capture DB/guard errors uniformly
        err = str(exc).strip() or exc.__class__.__name__
        retry_count = int(state.get("plan_retry_count") or 0)
        if retry_count < MAX_PLAN_RETRIES:
            return {
                "status": "retrying",
                "sample": None,
                "plan_feedback": (
                    f"Executing the SQL raised an error against {settings.sql_dialect}. "
                    f"Rewrite the query to avoid it. Error: {err}"
                ),
                "plan_feedback_source": "execution",
                "plan_retry_count": retry_count + 1,
            }
        return {
            "status": "execution_error",
            "sample": None,
            "assistant_text": "",
            "explanation": (
                f"SQL execution failed after {retry_count + 1} attempts on {settings.sql_dialect}. "
                f"Last error: {err}"
            ),
            "limitations": [f"Last error: {err}"],
            "plan_feedback": None,
            "plan_feedback_source": None,
        }

    sample = {
        "row_count": result["row_count"],
        "columns": result["columns"],
        "rows": result["rows"][:10],
    }
    update = {
        "status": "ok",
        "sample": sample,
        "assistant_text": str(state.get("assistant_text") or ""),
        "explanation": "",
        "limitations": [],
        "plan_feedback": None,
        "plan_feedback_source": None,
    }
    return update


def finalize_query_node(state: QueryAgentState) -> dict[str, Any]:
    """Ensure response consistency for non-executed paths."""
    session_id = str(state.get("session_id") or "default")
    SessionStore().record_turn(
        session_id,
        question=str(state.get("question") or ""),
        sql_candidate=state.get("sql_candidate"),
        status=str(state.get("status") or ""),
        clarification_question=state.get("clarification_question"),
        candidate_tables=list(state.get("candidate_tables") or []),
        intent=str(state.get("intent")) if state.get("intent") else None,
        assistant_summary=_assistant_turn_summary(state),
    )

    status = state.get("status")
    if status == "ok" or status == "blocked_missing_schema":
        return {}
    validator = state.get("validator") or {}
    if status == "needs_clarification":
        return {}
    if validator and (not validator.get("approved") or validator.get("needs_clarification")):
        update = {
            "status": "needs_clarification",
            "sample": None,
            "explanation": "",
            "limitations": [],
            "clarification_question": validator.get("clarification_question") or "",
        }
        return update
    return {}


def _heuristic_plan_and_sql(question: str, schema_context: dict[str, dict[str, Any]]) -> dict[str, Any]:
    q = question.lower()
    scores: list[tuple[str, int]] = []
    matched_columns: list[str] = []
    for table_name in _table_names(schema_context):
        columns = _table_columns(schema_context, table_name)
        score = 0
        if table_name.lower() in q:
            score += 3
        tokens = re.split(r"[_\s]+", table_name.lower())
        score += sum(1 for t in tokens if t and t in q)
        for col in columns:
            if col.lower() in q:
                score += 2
                matched_columns.append(col)
        if score > 0:
            scores.append((table_name, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    candidate_tables = [name for name, _ in scores[:2]]
    intent = "aggregation" if any(k in q for k in ("count", "how many", "cantidad", "total")) else "list"
    if not candidate_tables:
        available = ", ".join(sorted(_table_names(schema_context))[:5])
        return {
            "intent": intent,
            "candidate_tables": [],
            "candidate_columns": [],
            "needs_clarification": True,
            "clarification_question": (
                "I could not identify target tables. "
                f"Could you specify a table or domain? Available examples: {available}."
            ),
            "sql_candidate": "",
            "assistant_text": "I need clarification to choose the right target table.",
        }
    if len(candidate_tables) > 1 and len(scores) > 1 and scores[0][1] == scores[1][1]:
        return {
            "intent": intent,
            "candidate_tables": candidate_tables,
            "candidate_columns": list(dict.fromkeys(matched_columns)),
            "needs_clarification": True,
            "clarification_question": (
                f"Your query could refer to {candidate_tables[0]} or {candidate_tables[1]}. "
                "Which one should I use?"
            ),
            "sql_candidate": "",
            "assistant_text": "I see more than one possible interpretation and need you to choose one.",
        }
    table = candidate_tables[0]
    if intent == "aggregation":
        sql = f"SELECT COUNT(*) AS total FROM {table};"
    elif "order by" in q or "orden" in q:
        sql = f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT 20;"
    else:
        sql = f"SELECT * FROM {table} LIMIT 20;"
    return {
        "intent": intent,
        "candidate_tables": [table],
        "candidate_columns": list(dict.fromkeys(matched_columns)),
        "needs_clarification": False,
        "clarification_question": None,
        "sql_candidate": sql,
        "assistant_text": f"I generated a query to answer your request about '{table}'.",
    }


def _heuristic_validate(question: str, sql: str, state: QueryAgentState) -> dict[str, Any]:
    issues: list[str] = []
    try:
        validate_read_only_sql(sql)
    except ValueError as exc:
        issues.append(str(exc))
    if "count" in question.lower() or "cuantos" in question.lower():
        if "count(" not in sql.lower():
            issues.append("The question appears aggregate, but SQL does not use COUNT.")
    needs_clarification = bool(state.get("needs_clarification"))
    if needs_clarification:
        issues.append("Planner reported ambiguity.")
    approved = len(issues) == 0 and not needs_clarification
    return {
        "approved": approved,
        "risk_level": "low" if approved else "high",
        "issues": issues or ["OK"],
        "needs_clarification": needs_clarification,
        "clarification_question": state.get("clarification_question"),
    }


def _critic_to_state_update(
    validator: dict[str, Any],
    state: QueryAgentState | None = None,
) -> dict[str, Any]:
    """Translate the critic's validator payload into a state update.

    Three outcomes:
    1. Approved -> clear retry scratchpad and let the router execute the SQL.
    2. Rejected on fixable correctness/syntax (no user ambiguity) and retry
       budget remains -> set ``plan_feedback`` so the planner re-drafts the
       SQL. Route function sees ``plan_feedback`` and sends us back to planner.
    3. Needs user clarification, or retries exhausted -> set
       ``status=needs_clarification`` and bail out to finish.
    """
    if validator.get("approved") and not validator.get("needs_clarification"):
        return {
            "validator": validator,
            "plan_feedback": None,
            "plan_feedback_source": None,
        }

    retry_count = int((state or {}).get("plan_retry_count") or 0) if state else 0
    needs_user_clarification = bool(validator.get("needs_clarification"))
    can_retry = (
        state is not None
        and not needs_user_clarification
        and retry_count < MAX_PLAN_RETRIES
    )

    if can_retry:
        issues = [
            str(item).strip()
            for item in (validator.get("issues") or [])
            if isinstance(item, str) and str(item).strip() and str(item).strip().lower() != "ok"
        ]
        feedback = " ; ".join(issues) or "The critic did not approve the SQL."
        return {
            "validator": validator,
            "plan_feedback": feedback,
            "plan_feedback_source": "critic",
            "plan_retry_count": retry_count + 1,
        }

    return {
        "validator": validator,
        "status": "needs_clarification",
        "sample": None,
        "assistant_text": "",
        "explanation": "",
        "limitations": [],
        "clarification_question": validator.get("clarification_question")
        or "",
        "plan_feedback": None,
        "plan_feedback_source": None,
    }


def _parse_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list):
        value = "".join(str(part) for part in value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if "```" in text:
        text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_sql(value: Any) -> str | None:
    if isinstance(value, list):
        value = "".join(str(part) for part in value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if "```" in text:
        text = re.sub(r"```sql", "", text, flags=re.IGNORECASE).replace("```", "").strip()
    match = re.search(r"select[\s\S]*?;", text, flags=re.IGNORECASE)
    if match:
        return match.group(0).strip()
    if text.lower().startswith("select"):
        return text
    return None


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    return [str(item) for item in value if isinstance(item, str)]
