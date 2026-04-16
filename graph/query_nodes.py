"""LangGraph nodes for Query Agent.

Politica de memoria (iteracion 5):
- prepare: lee PersistentStore (preferencias por user_id) y SessionStore (contexto de sesion);
  escribe memory_context_text + persistent_prefs.
- planner / critic: solo lectura del estado (memory_context_text); el planificador usa texto
  enriquecido para NL2SQL y heuristicas.
- execute: solo lectura del estado.
- finalize: escribe SessionStore (ultima pregunta, SQL, filtros, supuestos, aclaraciones).
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
from prompts.query_agent import QUERY_CRITIC_SYSTEM_PROMPT, QUERY_PLANNER_SQL_SYSTEM_PROMPT
from settings import settings
from tools.mcp_sql_tool import MCPSQLQueryTool
from tools.sql_guard import validate_read_only_sql


def _build_memory_context_text(prefs: dict[str, str], snap: SessionSnapshot) -> str:
    lines: list[str] = [
        "Preferencias persistentes: "
        f"idioma={prefs.get('language', 'es')}; "
        f"formato_respuesta={prefs.get('format', 'markdown')}; "
        f"fechas={prefs.get('date_preference', 'iso')}; "
        f"estrictitud={prefs.get('strictness', 'normal')}."
    ]
    if snap.working_messages:
        dial: list[str] = []
        for m in snap.working_messages[-10:]:
            role = str(m.get("role", "?"))
            content = str(m.get("content", ""))[:420]
            dial.append(f"{role}: {content}")
        lines.append("Working memory (dialogo reciente, limite por tokens): " + " | ".join(dial))
    if (
        snap.last_question
        or snap.last_sql
        or snap.recent_filters
        or snap.assumptions
        or snap.clarifications
    ):
        lines.append(
            "Memoria de esta sesion (turnos previos): "
            f"ultima_pregunta={snap.last_question or '(ninguna)'}; "
            f"ultimo_sql={snap.last_sql or '(ninguno)'}; "
            f"filtros_recientes={snap.recent_filters}; "
            f"supuestos={snap.assumptions}; "
            f"aclaraciones_previas={snap.clarifications[-5:] if snap.clarifications else []}."
        )
    return "\n".join(lines)


def _assistant_turn_summary(state: QueryAgentState) -> str:
    """Resumen del turno para escribir en WorkingMemory (estilo DEMO02 assistant)."""
    status = str(state.get("status") or "")
    if status == "ok":
        sql = str(state.get("sql_candidate") or "")[:400]
        expl = str(state.get("explanation") or "")[:400]
        return f"SQL: {sql} | {expl}"
    if status == "needs_clarification":
        return str(state.get("clarification_question") or "Se requiere aclaracion.")[:500]
    if status == "blocked_missing_schema":
        return "Bloqueado: sin descripciones de schema aprobadas."
    expl = str(state.get("explanation") or "")
    if expl:
        return expl[:500]
    return f"[status={status}]"


def prepare_query_node(state: QueryAgentState) -> dict[str, Any]:
    """Load approved schema docs and block if unavailable."""
    user_id = str(state.get("user_id") or "default")
    session_id = str(state.get("session_id") or "default")
    store = SchemaDocsStore()
    latest = store.latest()
    doc = latest.get("document") if isinstance(latest, dict) else None
    schema_context: dict[str, list[str]] = {}
    if isinstance(doc, dict):
        tables = doc.get("tables")
        if isinstance(tables, list):
            for table in tables:
                if not isinstance(table, dict):
                    continue
                table_name = table.get("table_name")
                if not isinstance(table_name, str):
                    continue
                cols = table.get("columns") or []
                schema_context[table_name] = [
                    c.get("name")
                    for c in cols
                    if isinstance(c, dict) and isinstance(c.get("name"), str)
                ]
    if not schema_context:
        return {
            "schema_context": {},
            "status": "blocked_missing_schema",
            "sql_candidate": None,
            "sample": None,
            "explanation": "No hay descripciones de schema aprobadas para responder consultas NL2SQL.",
            "limitations": [
                "El Query Agent no puede ejecutar schema inspect.",
                "Primero debes generar/aprobar descripciones con el Schema Agent en la otra pestana.",
            ],
            "clarification_question": (
                "Cuando termines en el Schema Agent, volve a esta pestana y repeti la consulta."
            ),
            "validator": {},
            "candidate_tables": [],
            "user_id": user_id,
            "persistent_prefs": PersistentStore().get_preferences(user_id),
            "memory_context_text": "",
        }
    persistent = PersistentStore().get_preferences(user_id)
    snap = SessionStore().get_snapshot(session_id)
    memory_context_text = _build_memory_context_text(persistent, snap)
    return {
        "schema_context": schema_context,
        "user_id": user_id,
        "persistent_prefs": persistent,
        "memory_context_text": memory_context_text,
    }


def planner_node(state: QueryAgentState) -> dict[str, Any]:
    """Plan intent/tables and draft SQL candidate."""
    memory_block = str(state.get("memory_context_text") or "").strip()
    question = str(state.get("question") or "").strip()
    planning_input = (
        f"{memory_block}\n\nPregunta actual: {question}" if memory_block else question
    )
    schema_context = state.get("schema_context") or {}
    fallback = _heuristic_plan_and_sql(planning_input, schema_context)

    if not settings.llm_api_key.strip():
        return fallback

    model = get_chat_model(temperature=0)
    prompt = (
        f"{QUERY_PLANNER_SQL_SYSTEM_PROMPT}\n\n"
        f"Question (with memory context when present): {planning_input}\n"
        f"Schema context: {json.dumps(schema_context, ensure_ascii=False)}"
    )
    raw = model.invoke(prompt).content
    parsed = _parse_json_object(raw) or {}
    candidate_tables = _string_list(parsed.get("candidate_tables"), fallback["candidate_tables"])
    needs_clarification = bool(parsed.get("needs_clarification", fallback["needs_clarification"]))
    return {
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
        "explanation": (
            "No pude identificar una tabla objetivo para tu consulta."
            if needs_clarification or not candidate_tables
            else ""
        ),
        "limitations": (
            ["Sin tabla candidata no se intenta generar ni ejecutar SQL."]
            if needs_clarification or not candidate_tables
            else []
        ),
        "sample": None,
        "validator": {},
    }


def critic_node(state: QueryAgentState) -> dict[str, Any]:
    """Critic/validator node before executing SQL."""
    memory_block = str(state.get("memory_context_text") or "").strip()
    question = str(state.get("question") or "").strip()
    schema_context = state.get("schema_context") or {}
    sql_candidate = str(state.get("sql_candidate") or "").strip()
    fallback = _heuristic_validate(question, sql_candidate, state)

    if not settings.llm_api_key.strip():
        return _critic_to_state_update(fallback)

    model = get_chat_model(temperature=0)
    mem = (
        f"Contexto de memoria (persistente y de sesion):\n{memory_block}\n\n"
        if memory_block
        else ""
    )
    prompt = (
        f"{QUERY_CRITIC_SYSTEM_PROMPT}\n\n"
        f"{mem}"
        f"Pregunta del usuario: {question}\n"
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
    return _critic_to_state_update(validator)


def execute_query_node(state: QueryAgentState) -> dict[str, Any]:
    """Execute approved SQL using read-only MCP SQL tool."""
    sql_candidate = str(state.get("sql_candidate") or "").strip()
    result = MCPSQLQueryTool()._run(sql_candidate)
    sample = {
        "row_count": result["row_count"],
        "columns": result["columns"],
        "rows": result["rows"][:10],
    }
    table = (state.get("candidate_tables") or ["tabla no identificada"])[0]
    question = str(state.get("question") or "").strip()
    limitations = (
        ["La consulta no devolvio filas para los filtros implicitos."]
        if sample["row_count"] == 0
        else ["Resultado de muestra: solo se retornan hasta 10 filas en la respuesta."]
    )
    return {
        "status": "ok",
        "sample": sample,
        "explanation": (
            f"Interprete la pregunta '{question}' sobre la tabla '{table}'. "
            f"Se devolvieron {sample['row_count']} filas."
        ),
        "limitations": limitations,
    }


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
        return {
            "status": "needs_clarification",
            "sample": None,
            "explanation": "El validador marco riesgos o ambiguedad en la consulta.",
            "limitations": _string_list(validator.get("issues"), ["Se requiere aclaracion."]),
            "clarification_question": validator.get("clarification_question")
            or "Necesito una aclaracion para ejecutar una consulta precisa.",
        }
    return {}


def _heuristic_plan_and_sql(question: str, schema_context: dict[str, list[str]]) -> dict[str, Any]:
    q = question.lower()
    scores: list[tuple[str, int]] = []
    matched_columns: list[str] = []
    for table_name, columns in schema_context.items():
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
    intent = "aggregation" if any(k in q for k in ("count", "cuantos", "cantidad", "total")) else "list"
    if not candidate_tables:
        available = ", ".join(sorted(schema_context.keys())[:5])
        return {
            "intent": intent,
            "candidate_tables": [],
            "candidate_columns": [],
            "needs_clarification": True,
            "clarification_question": (
                "No pude identificar tablas objetivo. "
                f"Podes indicar una tabla o dominio? Ejemplos disponibles: {available}."
            ),
            "sql_candidate": "",
        }
    if len(candidate_tables) > 1 and len(scores) > 1 and scores[0][1] == scores[1][1]:
        return {
            "intent": intent,
            "candidate_tables": candidate_tables,
            "candidate_columns": list(dict.fromkeys(matched_columns)),
            "needs_clarification": True,
            "clarification_question": (
                f"Tu consulta puede referirse a {candidate_tables[0]} o {candidate_tables[1]}. "
                "Cual queres usar?"
            ),
            "sql_candidate": "",
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
    }


def _heuristic_validate(question: str, sql: str, state: QueryAgentState) -> dict[str, Any]:
    issues: list[str] = []
    try:
        validate_read_only_sql(sql)
    except ValueError as exc:
        issues.append(str(exc))
    if "count" in question.lower() or "cuantos" in question.lower():
        if "count(" not in sql.lower():
            issues.append("La pregunta parece agregada pero el SQL no usa COUNT.")
    needs_clarification = bool(state.get("needs_clarification"))
    if needs_clarification:
        issues.append("Planner reporto ambiguedad.")
    approved = len(issues) == 0 and not needs_clarification
    return {
        "approved": approved,
        "risk_level": "low" if approved else "high",
        "issues": issues or ["OK"],
        "needs_clarification": needs_clarification,
        "clarification_question": state.get("clarification_question"),
    }


def _critic_to_state_update(validator: dict[str, Any]) -> dict[str, Any]:
    if validator.get("approved") and not validator.get("needs_clarification"):
        return {"validator": validator}
    return {
        "validator": validator,
        "status": "needs_clarification",
        "sample": None,
        "explanation": "El validador marco riesgos o ambiguedad en la consulta.",
        "limitations": _string_list(validator.get("issues"), ["Se requiere aclaracion."]),
        "clarification_question": validator.get("clarification_question")
        or "Necesito una aclaracion para ejecutar una consulta precisa.",
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
