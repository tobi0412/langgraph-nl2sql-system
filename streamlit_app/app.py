"""
Interfaz Streamlit simple para el Schema Agent (HITL).

Referencia de estructura y UX: demos-estudiantes-main/EJ02-ReAct-LangGraph/spec-ui.md
(config por env, healthcheck, manejo de errores, layout claro).

Ejecucion local:
  pip install -e ".[ui]"
  streamlit run streamlit_app/app.py --server.port 8501

Docker: ver servicio ``streamlit`` en docker-compose.yml.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)

from observability.langsmith_setup import log_langsmith_status

log_langsmith_status()

from agents.schema_agent import SchemaAgentRunner
from streamlit_app.api_client import health_check
from streamlit_app.config_ui import get_api_base_url, get_api_timeout

logger = logging.getLogger(__name__)


def _init_session_state() -> None:
    defaults: dict[str, Any] = {
        "schema_runner": None,
        "schema_pending_hitl": False,
        "schema_last_result": None,
        "schema_session_id": str(uuid.uuid4()),
        "chat_status": [],
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _get_runner() -> SchemaAgentRunner:
    if st.session_state.schema_runner is None:
        st.session_state.schema_runner = SchemaAgentRunner()
    return st.session_state.schema_runner


def _render_sidebar() -> str:
    st.sidebar.title("Configuracion")
    api_base = st.sidebar.text_input(
        "API_BASE_URL",
        value=get_api_base_url(),
        help="Solo afecta el healthcheck del API FastAPI. El Schema Agent corre en este proceso.",
    )
    if st.sidebar.button("Verificar API", use_container_width=True):
        ok, msg = health_check(api_base, timeout=get_api_timeout())
        if ok:
            st.sidebar.success(msg)
        else:
            st.sidebar.error(msg)

    st.sidebar.divider()
    session_id = st.sidebar.text_input("Session ID (thread)", value=st.session_state.schema_session_id)
    if st.sidebar.button("Nueva sesion (nuevo thread)", use_container_width=True):
        st.session_state.schema_session_id = str(uuid.uuid4())
        st.session_state.schema_pending_hitl = False
        st.session_state.schema_last_result = None
        st.session_state.schema_runner = None
        st.rerun()

    st.sidebar.caption(
        "Requiere `DATABASE_URL`, `LLM_*` y opcionalmente `SCHEMA_DOCS_PATH` en el entorno."
    )
    return session_id


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


def main() -> None:
    st.set_page_config(
        page_title="NL2SQL — Schema Agent",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_session_state()

    session_id = _render_sidebar()

    st.title("Documentacion de schema (Human-in-the-loop)")
    st.markdown(
        "Genera borradores con el **Schema Agent** (LangGraph + `mcp_schema_inspect`), revisa el JSON y "
        "**aprueba**, **edita** o **rechaza** antes de persistir en disco."
    )

    col_a, col_b = st.columns((2, 1))
    with col_a:
        user_message = st.text_area(
            "Instruccion para el agente",
            value="Document the public schema for the DVD rental database.",
            height=100,
        )
        start = st.button("Iniciar documentacion", type="primary", use_container_width=True)

    with col_b:
        st.metric("Estado HITL", "Pendiente" if st.session_state.schema_pending_hitl else "Listo")
        reset_schema = st.checkbox(
            "Regenerar schema desde cero",
            value=False,
            help=(
                "Ignora el esquema persistido actual y fuerza modo primera vez "
                "(precarga metadata completa)."
            ),
        )

    if start:
        st.session_state.schema_pending_hitl = False
        st.session_state.schema_last_result = None
        with st.spinner("Consultando LLM y base de datos (puede tardar)..."):
            try:
                result = _get_runner().start(
                    session_id=session_id,
                    user_message=user_message,
                    reset_schema=reset_schema,
                )
            except Exception as exc:
                logger.exception("schema_start_failed")
                st.error(f"Error al ejecutar el grafo: {exc}")
                return
        st.session_state.schema_last_result = result
        st.session_state.schema_pending_hitl = bool(result.get("__interrupt__")) or bool(
            _draft_from_result(result)
        )
        st.session_state.schema_session_id = session_id

    result = st.session_state.schema_last_result
    if not result:
        st.info("Pulsa **Iniciar documentacion** para obtener un borrador y entrar en revision humana.")
        return

    draft = _draft_from_result(result)
    st.subheader("Resultado")
    if result.get("status") == "persisted" and not st.session_state.schema_pending_hitl:
        st.success("Documento persistido.")
        st.json(result.get("approved_document"))
        return

    if draft:
        st.markdown("#### Borrador (JSON)")
        st.json(draft)
    else:
        st.warning("No se pudo extraer `draft_document`. Respuesta cruda:")
        st.json({k: v for k, v in result.items() if k != "messages"})

    if st.session_state.schema_pending_hitl or result.get("__interrupt__"):
        st.divider()
        st.subheader("Revision humana (HITL)")
        action = st.radio(
            "Decision",
            ("approve", "edit", "reject"),
            horizontal=True,
            format_func=lambda x: {"approve": "Aprobar", "edit": "Editar JSON", "reject": "Rechazar"}[x],
        )
        edited_json = ""
        if action == "edit":
            edited_json = st.text_area(
                "Documento editado (JSON valido)",
                value=json.dumps(draft or {}, indent=2, ensure_ascii=False),
                height=320,
            )
        reason = ""
        if action == "reject":
            reason = st.text_input("Motivo (opcional)", value="")

        if st.button("Confirmar decision", type="primary"):
            human_feedback: dict[str, Any]
            if action == "approve":
                human_feedback = {"action": "approve"}
            elif action == "reject":
                human_feedback = {"action": "reject", "reason": reason or "rejected"}
            else:
                try:
                    parsed = json.loads(edited_json)
                except json.JSONDecodeError as exc:
                    st.error(f"JSON invalido: {exc}")
                    return
                human_feedback = {"action": "edit", "edited_document": parsed}

            with st.spinner("Persistiendo..."):
                try:
                    out = _get_runner().resume(session_id=session_id, human_feedback=human_feedback)
                except Exception as exc:
                    logger.exception("schema_resume_failed")
                    st.error(f"Error al reanudar: {exc}")
                    return

            st.session_state.schema_last_result = out
            st.session_state.schema_pending_hitl = False
            if out.get("status") == "rejected":
                st.warning(f"Rechazado: {out.get('error', '')}")
            elif out.get("status") == "persisted":
                st.success("Guardado correctamente.")
                st.json(out.get("approved_document"))
            else:
                st.error(out.get("error", str(out)))
            st.rerun()


if __name__ == "__main__":
    main()
