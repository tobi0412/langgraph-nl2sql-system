"""Streamlit workspace para el sistema NL2SQL (Schema Agent + Query Agent).

Dise\xf1o moderno oscuro con:
- Una pesta\xf1a por agente (Schema, Query).
- Chat persistente: cada mensaje del usuario y respuesta quedan en pantalla.
- Indicador en vivo del progreso del grafo (nodos, tool use, razonamiento)
  al estilo de chats de LLM conocidos (usando ``graph.stream``).
- En la pesta\xf1a Schema, bot\xf3n para resetear el contexto del schema.

Ejecucion local:
  pip install -e ".[ui]"
  streamlit run streamlit_app/app.py --server.port 8501
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)

from observability.langsmith_setup import log_langsmith_status

log_langsmith_status()

from agents.query_agent import QueryAgent
from agents.schema_agent import SchemaAgentRunner
from streamlit_app.api_client import health_check
from streamlit_app.config_ui import get_api_base_url, get_api_timeout

logger = logging.getLogger(__name__)


SCHEMA_NODE_LABELS: dict[str, dict[str, str]] = {
    "agent": {
        "pending": "Razonar sobre la documentaci\xf3n del schema",
        "active": "Razonando sobre la documentaci\xf3n del schema...",
        "done": "Razonamiento completado",
        "icon": "psychology",
    },
    "tools": {
        "pending": "Inspeccionar base de datos con mcp_schema_inspect",
        "active": "Inspeccionando base de datos (mcp_schema_inspect)...",
        "done": "Metadata del schema recopilada",
        "icon": "build",
    },
    "format_draft": {
        "pending": "Formatear borrador del documento",
        "active": "Preparando borrador JSON del schema...",
        "done": "Borrador listo para revisi\xf3n",
        "icon": "description",
    },
    "human_gate": {
        "pending": "Esperar revisi\xf3n humana (HITL)",
        "active": "Esperando tu revisi\xf3n...",
        "done": "Revisi\xf3n recibida",
        "icon": "rate_review",
    },
    "persist_approved": {
        "pending": "Persistir documento aprobado",
        "active": "Guardando documento aprobado...",
        "done": "Documento persistido",
        "icon": "save",
    },
}

QUERY_NODE_LABELS: dict[str, dict[str, str]] = {
    "prepare": {
        "pending": "Cargar schema aprobado y memoria de sesi\xf3n",
        "active": "Cargando schema aprobado y memoria de sesi\xf3n...",
        "done": "Contexto cargado",
        "icon": "memory",
    },
    "planner": {
        "pending": "Planificar consulta NL2SQL",
        "active": "Planificando consulta y generando SQL candidato...",
        "done": "Plan y SQL generados",
        "icon": "auto_awesome",
    },
    "critic": {
        "pending": "Validar SQL (guard de read-only)",
        "active": "Validando SQL candidato...",
        "done": "SQL validado",
        "icon": "verified_user",
    },
    "execute": {
        "pending": "Ejecutar SQL contra PostgreSQL",
        "active": "Ejecutando SQL contra PostgreSQL...",
        "done": "Resultados obtenidos",
        "icon": "play_arrow",
    },
    "finish": {
        "pending": "Finalizar respuesta",
        "active": "Finalizando respuesta y actualizando memoria...",
        "done": "Respuesta lista",
        "icon": "check_circle",
    },
}


# =============================================================================
# Session state & CSS
# =============================================================================


def _load_css() -> None:
    css_path = Path(__file__).with_name("styles.css")
    if not css_path.exists():
        return
    st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _init_session_state() -> None:
    defaults: dict[str, Any] = {
        "schema_runner": None,
        "query_agent": None,
        "schema_session_id": str(uuid.uuid4()),
        "schema_chat": [],
        "query_chat": [],
        "schema_pending_hitl": False,
        "schema_last_result": None,
        "schema_last_draft": None,
        "schema_force_reset": False,
        "health_ok": None,
        "health_msg": "",
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _get_runner() -> SchemaAgentRunner:
    if st.session_state.schema_runner is None:
        st.session_state.schema_runner = SchemaAgentRunner()
    return st.session_state.schema_runner


def _get_query_agent() -> QueryAgent:
    if st.session_state.query_agent is None:
        st.session_state.query_agent = QueryAgent()
    return st.session_state.query_agent


# =============================================================================
# Header & sidebar
# =============================================================================


def _render_agent_header(
    *,
    icon: str,
    title: str,
    subtitle: str,
    action_label: str,
    action_key: str,
    on_action,
    action_help: str = "",
) -> None:
    """Header superior mostrando el agente activo + acci\xf3n a la derecha."""
    left, right = st.columns([6, 1.2], vertical_alignment="center")
    with left:
        st.markdown(
            f"""
            <div class="agent-hero">
              <div class="agent-hero-logo">
                <span class="material-symbols-rounded">{icon}</span>
              </div>
              <div class="agent-hero-text">
                <h1>{title}</h1>
                <p>{subtitle}</p>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        clicked = st.button(
            action_label,
            use_container_width=True,
            key=action_key,
            help=action_help or None,
        )
        if clicked:
            on_action()


def _render_sidebar() -> str:
    with st.sidebar:
        st.markdown("### Configuraci\xf3n")

        api_base = st.text_input("API base URL", value=get_api_base_url())

        if st.button("Verificar salud del API", use_container_width=True):
            ok, msg = health_check(api_base, timeout=get_api_timeout())
            st.session_state.health_ok = ok
            st.session_state.health_msg = msg
            if ok:
                st.success(msg)
            else:
                st.error(msg)

        if st.session_state.health_ok is True and st.session_state.health_msg:
            st.caption(st.session_state.health_msg)
        elif st.session_state.health_ok is False:
            st.caption(st.session_state.health_msg)

        st.divider()
        st.markdown("### Sesi\xf3n")

        session_id = st.text_input(
            "Session ID (thread)",
            value=st.session_state.schema_session_id,
            help="Identificador de thread para checkpointer y memoria de sesi\xf3n",
        )
        if session_id != st.session_state.schema_session_id:
            st.session_state.schema_session_id = session_id

        if st.button("Nueva sesi\xf3n", use_container_width=True):
            st.session_state.schema_session_id = str(uuid.uuid4())
            st.session_state.schema_pending_hitl = False
            st.session_state.schema_last_result = None
            st.session_state.schema_last_draft = None
            st.session_state.schema_runner = None
            st.session_state.schema_force_reset = False
            st.session_state.schema_chat = []
            st.session_state.query_chat = []
            st.rerun()

        st.divider()
        st.caption("Runtime: LangGraph + Streamlit")

        return session_id


# =============================================================================
# Thinking / status rendering
# =============================================================================


def _thinking_step_html(step_state: str, icon: str, text: str) -> str:
    return (
        f'<div class="thinking-step {step_state}">'
        f'<span class="step-icon material-symbols-rounded">{icon}</span>'
        f'<span>{text}</span>'
        f"</div>"
    )


class ThinkingRenderer:
    """Renderiza una lista viva de pasos dentro de un placeholder."""

    def __init__(self, placeholder: Any, labels: dict[str, dict[str, str]]):
        self.placeholder = placeholder
        self.labels = labels
        self.order: list[str] = []
        self.state: dict[str, str] = {}

    def mark_active(self, node: str) -> None:
        info = self.labels.get(node)
        if not info:
            return
        if node not in self.state:
            self.order.append(node)
        for prev in self.order:
            if prev != node and self.state.get(prev) == "active":
                self.state[prev] = "done"
        self.state[node] = "active"
        self._render()

    def mark_done(self, node: str) -> None:
        info = self.labels.get(node)
        if not info:
            return
        if node not in self.state:
            self.order.append(node)
        self.state[node] = "done"
        self._render()

    def mark_all_done(self) -> None:
        for key in self.order:
            if self.state.get(key) == "active":
                self.state[key] = "done"
        self._render()

    def _render(self) -> None:
        html_parts: list[str] = []
        for node in self.order:
            info = self.labels.get(node, {})
            status = self.state.get(node, "pending")
            text = info.get(status, info.get("pending", node))
            icon = info.get("icon", "radio_button_unchecked")
            html_parts.append(_thinking_step_html(status, icon, text))
        self.placeholder.markdown("\n".join(html_parts), unsafe_allow_html=True)


def _stream_with_status(
    events: Iterable[dict[str, Any]],
    labels: dict[str, dict[str, str]],
    status_title: str,
) -> tuple[list[dict[str, Any]], Any]:
    """Consume events yielding dicts con kind y renderiza progreso temporalmente.

    Retorna (lista de eventos, placeholder para limpieza).
    El placeholder debe ser limpiado después de usar: placeholder.empty()
    """
    collected: list[dict[str, Any]] = []
    placeholder = st.empty()
    with placeholder.status(status_title, expanded=True) as status:
        inner_placeholder = st.empty()
        renderer = ThinkingRenderer(inner_placeholder, labels)
        last_node: str | None = None
        try:
            for event in events:
                collected.append(event)
                kind = event.get("kind")
                if kind == "node":
                    name = event.get("name", "")
                    if last_node and last_node != name:
                        renderer.mark_done(last_node)
                    renderer.mark_active(name)
                    last_node = name
                    time.sleep(0.05)
                elif kind == "interrupt":
                    renderer.mark_done(last_node) if last_node else None
                elif kind == "final":
                    renderer.mark_all_done()
                    status.update(label="Listo", state="complete", expanded=False)
                elif kind == "error":
                    renderer.mark_all_done()
                    status.update(label="Error", state="error", expanded=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("stream_render_failed")
            collected.append({"kind": "error", "message": str(exc)})
            status.update(label=f"Error: {exc}", state="error", expanded=True)
    return collected, placeholder


# =============================================================================
# Chat message rendering
# =============================================================================


def _bubble_safe(content: str) -> str:
    """Escapa HTML y convierte **bold** simple a <strong>."""
    safe = html.escape(str(content))
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = safe.replace("\n", "<br/>")
    return safe


def _render_user_msg(content: str) -> None:
    st.markdown(
        f'<div class="user-bubble-row"><div class="user-bubble">{_bubble_safe(content)}</div></div>',
        unsafe_allow_html=True,
    )


def _render_assistant_msg(payload: dict[str, Any]) -> None:
    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        _render_assistant_payload(payload)


def _render_assistant_payload(payload: dict[str, Any]) -> None:
    kind = payload.get("kind")
    if kind == "schema_result":
        _render_schema_assistant(payload)
    elif kind == "schema_hitl_done":
        _render_schema_hitl_done(payload)
    elif kind == "query_result":
        _render_query_assistant(payload)
    elif kind == "error":
        st.error(payload.get("message", "Error desconocido"))
    elif kind == "info":
        st.info(payload.get("message", ""))
    else:
        st.write(payload.get("message", ""))


# ---------- Schema render ----------


def _render_schema_assistant(payload: dict[str, Any]) -> None:
    result = payload.get("result") or {}
    draft = payload.get("draft")
    status = result.get("status")
    pending_hitl = payload.get("pending_hitl", False)

    if status == "persisted":
        st.markdown(
            '<span class="status-pill ok">'
            '<span class="material-symbols-rounded" style="font-size:14px;">check_circle</span>'
            "Documento persistido</span>",
            unsafe_allow_html=True,
        )
        st.markdown("#### Documento aprobado")
        st.json(result.get("approved_document"))
        return

    if pending_hitl and draft:
        st.markdown(
            '<span class="status-pill pending">'
            '<span class="material-symbols-rounded" style="font-size:14px;">rate_review</span>'
            "Borrador listo \u2014 requiere tu revisi\xf3n</span>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "He preparado un borrador del documento de schema. "
            "Revisa el JSON y decide si **aprobar**, **editar** o **rechazar**."
        )
        with st.expander("Ver borrador completo (JSON)", expanded=False):
            st.json(draft)
        return

    if status == "rejected":
        st.markdown(
            '<span class="status-pill blocked">'
            '<span class="material-symbols-rounded" style="font-size:14px;">cancel</span>'
            "Rechazado</span>",
            unsafe_allow_html=True,
        )
        st.write(result.get("error", "Documento rechazado."))
        return

    if status == "error":
        st.error(result.get("error", "Error desconocido"))
        return

    st.info("Resultado recibido.")
    st.json({k: v for k, v in result.items() if k != "messages"})


def _render_schema_hitl_done(payload: dict[str, Any]) -> None:
    action = payload.get("action", "")
    result = payload.get("result") or {}
    status = result.get("status")
    if status == "persisted":
        st.markdown(
            '<span class="status-pill ok">'
            '<span class="material-symbols-rounded" style="font-size:14px;">check_circle</span>'
            f"Documento {('editado y ' if action == 'edit' else '')}aprobado</span>",
            unsafe_allow_html=True,
        )
        st.markdown("El documento qued\xf3 persistido y disponible para el Query Agent.")
        with st.expander("Ver documento aprobado", expanded=False):
            st.json(result.get("approved_document"))
    elif status == "rejected":
        st.markdown(
            '<span class="status-pill blocked">'
            '<span class="material-symbols-rounded" style="font-size:14px;">cancel</span>'
            "Rechazado</span>",
            unsafe_allow_html=True,
        )
        st.write(result.get("error", "Documento rechazado."))
    else:
        st.warning(f"Estado inesperado: {status}")
        st.json(result)


# ---------- Query render ----------


def _render_query_assistant(payload: dict[str, Any]) -> None:
    result = payload.get("result") or {}
    status = result.get("status")

    if status == "ok":
        st.markdown(
            '<span class="status-pill ok">'
            '<span class="material-symbols-rounded" style="font-size:14px;">check_circle</span>'
            "Consulta ejecutada</span>",
            unsafe_allow_html=True,
        )
    elif status == "blocked_missing_schema":
        st.markdown(
            '<span class="status-pill blocked">'
            '<span class="material-symbols-rounded" style="font-size:14px;">block</span>'
            "Falta schema aprobado</span>",
            unsafe_allow_html=True,
        )
    elif status == "needs_clarification":
        st.markdown(
            '<span class="status-pill info">'
            '<span class="material-symbols-rounded" style="font-size:14px;">help</span>'
            "Requiere aclaraci\xf3n</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<span class="status-pill info">{status}</span>',
            unsafe_allow_html=True,
        )

    if result.get("explanation"):
        st.markdown(result["explanation"])

    if result.get("clarification_question"):
        st.markdown("**Aclaraci\xf3n sugerida:** " + str(result["clarification_question"]))

    if result.get("sql_final"):
        st.markdown("#### SQL final")
        st.code(result["sql_final"], language="sql")

    sample = result.get("sample")
    if isinstance(sample, dict):
        rows = sample.get("rows") or []
        columns = sample.get("columns") or []
        st.markdown(f"#### Muestra de resultados \u2014 {sample.get('row_count', 0)} filas")
        if rows and columns:
            table_rows = [dict(zip(columns, row)) for row in rows]
            st.dataframe(table_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("Sin filas para mostrar.")


# =============================================================================
# Schema tab
# =============================================================================


def _append_schema(role: str, payload: Any) -> None:
    st.session_state.schema_chat.append({"role": role, "payload": payload})


def _render_schema_chat_history() -> None:
    for msg in st.session_state.schema_chat:
        if msg["role"] == "user":
            _render_user_msg(str(msg["payload"]))
        else:
            _render_assistant_msg(msg["payload"])


def _render_schema_empty_state() -> None:
    st.markdown(
        """
        <div class="chat-empty">
          <div class="icon"><span class="material-symbols-rounded">schema</span></div>
          <h3>Comenc\xe9 documentando tu base de datos</h3>
          <p>El Schema Agent inspecciona PostgreSQL y genera un documento JSON
          con tablas, columnas y descripciones. Vos aprob\xe1s, edit\xe1s o rechaz\xe1s
          antes de persistir.</p>
          <div class="hints">
            <span class="hint-pill">Document the public schema for the DVD rental database</span>
            <span class="hint-pill">Actualiza s\xf3lo la descripci\xf3n de la tabla film</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _draft_from_result(result: dict[str, Any]) -> dict[str, Any] | None:
    draft = result.get("draft_document")
    if isinstance(draft, dict) and draft:
        return draft
    interrupts = result.get("__interrupt__")
    if interrupts:
        try:
            first = interrupts[0]
            value = getattr(first, "value", first)
            if isinstance(value, dict) and isinstance(value.get("draft_document"), dict):
                return value["draft_document"]
        except (IndexError, TypeError, AttributeError):
            pass
    return None


def _render_schema_hitl_controls(session_id: str) -> None:
    if not st.session_state.schema_pending_hitl:
        return
    draft = st.session_state.schema_last_draft
    st.markdown("---")
    st.markdown("#### Revisi\xf3n humana (HITL)")
    action = st.radio(
        "Decisi\xf3n",
        ("approve", "edit", "reject"),
        horizontal=True,
        format_func=lambda x: {
            "approve": "Aprobar",
            "edit": "Editar JSON",
            "reject": "Rechazar",
        }[x],
        key="schema_hitl_action",
    )
    edited_json = ""
    if action == "edit":
        edited_json = st.text_area(
            "Documento editado (JSON v\xe1lido)",
            value=json.dumps(draft or {}, indent=2, ensure_ascii=False),
            height=320,
            key="schema_hitl_edit",
        )
    reason = ""
    if action == "reject":
        reason = st.text_input(
            "Motivo (opcional)", value="", key="schema_hitl_reason"
        )

    confirm = st.button("Confirmar decisi\xf3n", type="primary", key="schema_hitl_confirm")
    if not confirm:
        return

    if action == "approve":
        human_feedback: dict[str, Any] = {"action": "approve"}
    elif action == "reject":
        human_feedback = {"action": "reject", "reason": reason or "rejected"}
    else:
        try:
            parsed = json.loads(edited_json)
        except json.JSONDecodeError as exc:
            st.error(f"JSON inv\xe1lido: {exc}")
            return
        human_feedback = {"action": "edit", "edited_document": parsed}

    _append_schema("user", _hitl_user_label(action, reason))

    events, thinking_placeholder = _stream_with_status(
        _get_runner().stream_resume(
            session_id=session_id, human_feedback=human_feedback
        ),
        SCHEMA_NODE_LABELS,
        "Aplicando decisi\xf3n del humano...",
    )
    final = _find_final(events)
    errors = [e for e in events if e.get("kind") == "error"]
    if errors:
        msg = errors[0].get("message", "Error ejecutando resume")
        _append_schema("assistant", {"kind": "error", "message": msg})
        thinking_placeholder.empty()
        st.rerun()
        return
    if not final:
        _append_schema(
            "assistant",
            {"kind": "error", "message": "No se recibi\xf3 resultado final"},
        )
        thinking_placeholder.empty()
        st.rerun()
        return
    result = final.get("state") or {}
    st.session_state.schema_last_result = result
    st.session_state.schema_pending_hitl = False
    st.session_state.schema_last_draft = None
    _append_schema(
        "assistant",
        {"kind": "schema_hitl_done", "action": action, "result": result},
    )
    thinking_placeholder.empty()
    st.rerun()


def _hitl_user_label(action: str, reason: str) -> str:
    if action == "approve":
        return "Decisi\xf3n HITL: **Aprobar**"
    if action == "edit":
        return "Decisi\xf3n HITL: **Editar JSON** (con cambios)"
    suffix = f" \u2014 motivo: {reason}" if reason else ""
    return "Decisi\xf3n HITL: **Rechazar**" + suffix


def _reset_schema_context() -> None:
    st.session_state.schema_force_reset = True
    st.session_state.schema_pending_hitl = False
    st.session_state.schema_last_result = None
    st.session_state.schema_last_draft = None
    st.session_state.schema_runner = None
    st.session_state.schema_chat = []
    st.toast("Contexto del Schema Agent reseteado.", icon=":material/restart_alt:")
    st.rerun()


def _render_schema_tab(session_id: str) -> None:
    _render_agent_header(
        icon="schema",
        title="Schema Agent",
        subtitle="Documenta el schema con HITL y persiste el JSON aprobado",
        action_label="Resetear schema",
        action_key="schema_reset_btn",
        on_action=_reset_schema_context,
        action_help="Descarta el contexto actual y fuerza regenerar desde cero en el pr\xf3ximo mensaje.",
    )

    if st.session_state.schema_force_reset:
        st.caption(
            "Modo reset activo: el pr\xf3ximo mensaje regenerar\xe1 la documentaci\xf3n desde cero."
        )

    chat_area = st.container(height=_chat_area_height(), border=False)
    with chat_area:
        st.markdown('<div class="chat-scroll">', unsafe_allow_html=True)
        if not st.session_state.schema_chat:
            _render_schema_empty_state()
        else:
            _render_schema_chat_history()
        _render_schema_hitl_controls(session_id)
        st.markdown("</div>", unsafe_allow_html=True)

    prompt = st.chat_input(
        "Escrib\xed una instrucci\xf3n para documentar el schema...",
        key="schema_chat_input",
        disabled=st.session_state.schema_pending_hitl,
    )
    if not prompt:
        return

    _append_schema("user", prompt)
    with chat_area:
        _render_user_msg(prompt)

        reset_schema = bool(st.session_state.schema_force_reset)
        events, thinking_placeholder = _stream_with_status(
            _get_runner().stream_start(
                session_id=session_id,
                user_message=prompt,
                reset_schema=reset_schema,
            ),
            SCHEMA_NODE_LABELS,
            "Ejecutando Schema Agent...",
        )
    st.session_state.schema_force_reset = False

    errors = [e for e in events if e.get("kind") == "error"]
    if errors:
        msg = errors[0].get("message", "Error ejecutando Schema Agent")
        _append_schema("assistant", {"kind": "error", "message": msg})
        st.rerun()
        return

    final = _find_final(events)
    if not final:
        _append_schema(
            "assistant",
            {"kind": "error", "message": "No se recibi\xf3 resultado final del grafo"},
        )
        st.rerun()
        return

    result = final.get("state") or {}
    draft = _draft_from_result(result)
    pending = bool(result.get("__interrupt__")) or bool(draft)
    st.session_state.schema_last_result = result
    st.session_state.schema_last_draft = draft
    st.session_state.schema_pending_hitl = pending
    _append_schema(
        "assistant",
        {
            "kind": "schema_result",
            "result": result,
            "draft": draft,
            "pending_hitl": pending,
        },
    )

    thinking_placeholder.empty()
    with chat_area:
        _render_schema_chat_history()
    st.rerun()


# =============================================================================
# Query tab
# =============================================================================


def _append_query(role: str, payload: Any) -> None:
    st.session_state.query_chat.append({"role": role, "payload": payload})


def _render_query_chat_history() -> None:
    for msg in st.session_state.query_chat:
        if msg["role"] == "user":
            _render_user_msg(str(msg["payload"]))
        else:
            _render_assistant_msg(msg["payload"])


def _render_query_empty_state() -> None:
    st.markdown(
        """
        <div class="chat-empty">
          <div class="icon"><span class="material-symbols-rounded">terminal</span></div>
          <h3>Preguntas en lenguaje natural \u2192 SQL</h3>
          <p>El Query Agent usa el schema aprobado por el Schema Agent para
          planificar, validar y ejecutar SQL de solo lectura contra PostgreSQL.</p>
          <div class="hints">
            <span class="hint-pill">\xbfCu\xe1ntos registros hay en rental?</span>
            <span class="hint-pill">Lista los primeros 10 actores</span>
            <span class="hint-pill">\xbfQu\xe9 pel\xedculas tienen rating PG?</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _clear_query_chat() -> None:
    st.session_state.query_chat = []
    st.rerun()


def _render_query_tab(session_id: str) -> None:
    _render_agent_header(
        icon="terminal",
        title="Query Agent",
        subtitle="NL2SQL con planner, critic y ejecuci\xf3n read-only contra PostgreSQL",
        action_label="Limpiar chat",
        action_key="query_clear_btn",
        on_action=_clear_query_chat,
        action_help="Borra el historial de chat de este agente.",
    )

    chat_area = st.container(height=_chat_area_height(), border=False)
    with chat_area:
        st.markdown('<div class="chat-scroll">', unsafe_allow_html=True)
        if not st.session_state.query_chat:
            _render_query_empty_state()
        else:
            _render_query_chat_history()
        st.markdown("</div>", unsafe_allow_html=True)

    question = st.chat_input(
        "Escrib\xed tu consulta en lenguaje natural...",
        key="query_chat_input",
    )
    if not question:
        return

    _append_query("user", question)
    with chat_area:
        _render_user_msg(question)

        events, thinking_placeholder = _stream_with_status(
            _get_query_agent().stream(question, session_id=session_id),
            QUERY_NODE_LABELS,
            "Ejecutando Query Agent...",
        )

    errors = [e for e in events if e.get("kind") == "error"]
    if errors:
        msg = errors[0].get("message", "Error ejecutando Query Agent")
        _append_query("assistant", {"kind": "error", "message": msg})
        st.rerun()
        return

    final = _find_final(events)
    if not final:
        _append_query(
            "assistant",
            {"kind": "error", "message": "No se recibi\xf3 respuesta final del grafo"},
        )
        st.rerun()
        return
    response = final.get("response") or {}
    _append_query("assistant", {"kind": "query_result", "result": response})

    thinking_placeholder.empty()
    with chat_area:
        _render_query_chat_history()
    st.rerun()


# =============================================================================
# Helpers
# =============================================================================


def _find_final(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for evt in reversed(events):
        if evt.get("kind") == "final":
            return evt
    return None


def _chat_area_height() -> int:
    """Altura responsiva del area de chat basada en altura de viewport."""
    try:
        session_state = st.session_state
        if not hasattr(session_state, "_window_height"):
            session_state._window_height = 800
        available = int(session_state._window_height * 0.58)
        return max(400, min(available, 800))
    except Exception:
        return 700


# =============================================================================
# Main
# =============================================================================


def main() -> None:
    st.set_page_config(
        page_title="NL2SQL Workspace",
        page_icon=":material/hub:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_session_state()
    _load_css()

    session_id = _render_sidebar()

    tab_schema, tab_query = st.tabs(["Schema Agent", "Query Agent"])
    with tab_schema:
        _render_schema_tab(session_id)
    with tab_query:
        _render_query_tab(session_id)


if __name__ == "__main__":
    main()
