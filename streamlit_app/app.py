"""Streamlit workspace for the NL2SQL system (Schema Agent + Query Agent).

Modern dark UI with:
- One tab per agent (Schema, Query).
- Persistent chat: each user/assistant turn stays on screen.
- Live graph-progress indicator (nodes, tool use, reasoning) while processing
  using ``graph.stream``.
- Schema tab includes a button to reset schema context.

Local run:
  pip install -e ".[ui]"
  streamlit run streamlit_app/app.py --server.port 8501
"""

from __future__ import annotations

import html
import logging
import re
import threading
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)

from observability.langsmith_setup import log_langsmith_status

log_langsmith_status()

from agents.query_agent import QueryAgent
from agents.schema_agent import SchemaAgentRunner
from memory.schema_docs_store import SchemaDocsStore
from streamlit_app.api_client import health_check
from streamlit_app.config_ui import get_api_base_url, get_api_timeout

logger = logging.getLogger(__name__)


SCHEMA_NODE_LABELS: dict[str, dict[str, str]] = {
    "agent": {
        "pending": "Reason about schema documentation",
        "active": "Reasoning about schema documentation...",
        "done": "Reasoning complete",
        "icon": "psychology",
    },
    "tools": {
        "pending": "Inspect database with mcp_schema_inspect",
        "active": "Inspecting database (mcp_schema_inspect)...",
        "done": "Schema metadata collected",
        "icon": "build",
    },
    "format_draft": {
        "pending": "Format draft document",
        "active": "Preparing schema JSON draft...",
        "done": "Draft ready for review",
        "icon": "description",
    },
    "human_gate": {
        "pending": "Wait for human review (HITL)",
        "active": "Waiting for your review...",
        "done": "Review received",
        "icon": "rate_review",
    },
    "persist_approved": {
        "pending": "Persist approved document",
        "active": "Saving approved document...",
        "done": "Document persisted",
        "icon": "save",
    },
}

QUERY_NODE_LABELS: dict[str, dict[str, str]] = {
    "prepare": {
        "pending": "Load approved schema and session memory",
        "active": "Loading approved schema and session memory...",
        "done": "Context loaded",
        "icon": "memory",
    },
    "planner": {
        "pending": "Plan NL2SQL query",
        "active": "Planning query and generating SQL candidate...",
        "done": "Plan and SQL generated",
        "icon": "auto_awesome",
    },
    "critic": {
        "pending": "Validate SQL (read-only guard)",
        "active": "Validating SQL candidate...",
        "done": "SQL validated",
        "icon": "verified_user",
    },
    "execute": {
        "pending": "Execute SQL against PostgreSQL",
        "active": "Executing SQL against PostgreSQL...",
        "done": "Results retrieved",
        "icon": "play_arrow",
    },
    "finish": {
        "pending": "Finalize response",
        "active": "Finalizing response and updating memory...",
        "done": "Response ready",
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
        "schema_docs_store": None,
        "schema_auto_generate": False,
        "active_stream": None,
        "pending_tab_switch": None,
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


def _get_schema_docs_store() -> SchemaDocsStore:
    if st.session_state.schema_docs_store is None:
        st.session_state.schema_docs_store = SchemaDocsStore()
    return st.session_state.schema_docs_store


def _has_approved_schema() -> bool:
    try:
        return bool(_get_schema_docs_store().latest())
    except Exception:  # noqa: BLE001
        return False


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
    """Top header showing active agent + action button."""
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
        st.markdown("### Configuration")

        api_base = st.text_input("API base URL", value=get_api_base_url())

        if st.button("Check API health", use_container_width=True):
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
        st.markdown("### Session")

        session_id = st.text_input(
            "Session ID (thread)",
            value=st.session_state.schema_session_id,
            help="Thread identifier for checkpointer and session memory",
        )
        if session_id != st.session_state.schema_session_id:
            st.session_state.schema_session_id = session_id

        if st.button("New session", use_container_width=True):
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


_STREAM_POLL_SECONDS = 0.25


def _start_stream_worker(
    *,
    kind: str,
    generator_factory: Callable[[], Any],
    labels: dict[str, dict[str, str]],
    title: str,
    on_done: Callable[[dict[str, Any]], None],
) -> None:
    """Spawn a daemon thread that iterates the generator cooperatively.

    The active stream is tracked under ``st.session_state.active_stream`` and
    consumed by :func:`_render_active_stream_fragment`, which polls the queue
    every ``_STREAM_POLL_SECONDS`` to render live progress and a Stop button.
    When the stream terminates (naturally, via user-requested cancel, or due to
    an error), ``on_done(result)`` is invoked on the main thread.
    """
    event_q: Queue = Queue()
    cancel_evt = threading.Event()
    done_evt = threading.Event()
    shared: dict[str, Any] = {
        "events": [],
        "error": None,
        "cancelled": False,
    }

    def _worker() -> None:
        gen = None
        try:
            gen = generator_factory()
            for event in gen:
                if cancel_evt.is_set():
                    shared["cancelled"] = True
                    break
                shared["events"].append(event)
                event_q.put(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("stream_worker_failed")
            shared["error"] = str(exc)
            err_evt = {"kind": "error", "message": str(exc)}
            shared["events"].append(err_evt)
            event_q.put(err_evt)
        finally:
            if gen is not None:
                try:
                    gen.close()
                except Exception:  # noqa: BLE001
                    pass
            done_evt.set()

    thread = threading.Thread(target=_worker, name=f"stream-{kind}", daemon=True)
    thread.start()

    st.session_state.active_stream = {
        "kind": kind,
        "queue": event_q,
        "cancel": cancel_evt,
        "done": done_evt,
        "shared": shared,
        "labels": labels,
        "title": title,
        "on_done": on_done,
        "order": [],
        "node_state": {},
        "stopping": False,
    }


@st.fragment(run_every=_STREAM_POLL_SECONDS)
def _render_active_stream_fragment() -> None:
    """Live thinking-widget renderer for the current active stream.

    Runs as a Streamlit fragment that auto-reruns every
    ``_STREAM_POLL_SECONDS`` seconds. Drains the worker queue (non-blocking),
    updates the per-node state, and — once the worker signals ``done`` —
    invokes ``on_done(...)`` and triggers a full app rerun so the normal
    chat/HITL UI takes over again.
    """
    active = st.session_state.get("active_stream")
    if not active:
        return

    labels: dict[str, dict[str, str]] = active.get("labels") or {}

    # Drain the node queue and track which graph nodes have fired. We only
    # register nodes that have an entry in `labels` — internal-only nodes
    # (e.g. `prefs_finalize`) are intentionally hidden from the UI.
    order: list[str] = active.setdefault("order", [])
    node_state: dict[str, int] = active.setdefault("node_state", {})
    while True:
        try:
            evt = active["queue"].get_nowait()
        except Empty:
            break
        if not isinstance(evt, dict) or evt.get("kind") != "node":
            continue
        node_name = str(evt.get("name") or "").strip()
        if not node_name or node_name == "__interrupt__":
            continue
        if node_name not in labels:
            continue
        order.append(node_name)
        node_state[node_name] = node_state.get(node_name, 0) + 1

    done = active["done"].is_set()
    shared = active["shared"]
    cancelled = done and shared["cancelled"]
    error = shared["error"] if done else None
    stopping = bool(active.get("stopping"))

    # Dedupe the fired nodes preserving first-seen order. Each node shows
    # once; retries are surfaced as an `(×N)` suffix on the done line.
    seen: set[str] = set()
    fired_in_order: list[str] = []
    for n in order:
        if n not in seen:
            fired_in_order.append(n)
            seen.add(n)

    # LangGraph emits an event when a node finishes, so the "currently
    # running" step is the first label that hasn't shown up in `order` yet.
    label_order = list(labels.keys())
    current_node: str | None = next(
        (n for n in label_order if n not in seen),
        None,
    )

    if error:
        outer_label, outer_state = f"Error: {error}", "error"
    elif cancelled:
        outer_label, outer_state = "Cancelled", "error"
    elif done:
        outer_label, outer_state = "Completed", "complete"
    elif stopping:
        outer_label, outer_state = "Stopping after current step...", "running"
    elif current_node and labels.get(current_node, {}).get("active"):
        outer_label = str(labels[current_node]["active"])
        outer_state = "running"
    else:
        outer_label, outer_state = active["title"], "running"

    with st.status(outer_label, state=outer_state, expanded=True):
        for n in fired_in_order:
            meta = labels.get(n) or {}
            count = node_state.get(n, 1)
            suffix = f" (×{count})" if count > 1 else ""
            done_label = str(meta.get("done", n)) + suffix
            st.markdown(
                '<div class="thinking-step done">'
                '<span class="step-icon">check_circle</span>'
                f'<span>{done_label}</span>'
                "</div>",
                unsafe_allow_html=True,
            )
        if not done and current_node:
            meta = labels.get(current_node) or {}
            active_label = str(meta.get("active", current_node))
            st.markdown(
                '<div class="thinking-step active">'
                '<span class="thinking-spinner"></span>'
                f'<span>{active_label}</span>'
                "</div>",
                unsafe_allow_html=True,
            )

    if not done:
        return

    on_done = active["on_done"]
    events = list(shared["events"])
    st.session_state.active_stream = None
    try:
        on_done({"events": events, "error": error, "cancelled": cancelled})
    except Exception as exc:  # noqa: BLE001
        logger.exception("stream_on_done_failed")
        st.error(f"Failed to finalize: {exc}")
    st.rerun(scope="app")


def _render_stop_button(*, key: str) -> None:
    """Render a destructive-styled Stop button that lives where send would.

    Cancellation is cooperative: the worker checks the cancel flag between
    graph events, so if LangGraph is blocked on a long LLM call the actual
    termination happens when that call returns. We reflect this in the UI so
    the user isn't left wondering whether the click registered.
    """
    active = st.session_state.get("active_stream")
    if not active:
        return
    stopping = bool(active.get("stopping"))
    with st.container():
        st.markdown('<div class="stream-stop-slot"></div>', unsafe_allow_html=True)
        label = "Stopping..." if stopping else "Stop"
        icon = ":material/hourglass_top:" if stopping else ":material/stop_circle:"
        clicked = st.button(
            label,
            key=key,
            use_container_width=True,
            icon=icon,
            disabled=stopping,
        )
        if clicked:
            active["cancel"].set()
            active["stopping"] = True
            st.toast(
                "Stop requested, will finish current step first.",
                icon=":material/stop_circle:",
            )
            st.rerun(scope="app")


def _render_tab_switch_js() -> None:
    """Programmatically click a Streamlit tab once per pending request.

    Streamlit dedupes component renders by HTML content, so we include a fresh
    nonce on each call — otherwise the second invocation with identical HTML
    would be a no-op and the tab would not switch. Also retries briefly in case
    the tab buttons haven't mounted yet on the first tick.
    """
    target = st.session_state.get("pending_tab_switch")
    if not target:
        return
    index = 0 if target == "schema" else 1
    nonce = uuid.uuid4().hex
    components.html(
        f"""
        <!-- tab-switch nonce={nonce} -->
        <script>
        (function() {{
            var tried = 0;
            function tryClick() {{
                tried += 1;
                var doc = window.parent.document;
                var tabs = doc.querySelectorAll('button[role="tab"]');
                if (tabs && tabs[{index}]) {{
                    tabs[{index}].click();
                    return;
                }}
                if (tried < 12) setTimeout(tryClick, 50);
            }}
            setTimeout(tryClick, 30);
        }})();
        </script>
        """,
        height=0,
    )
    st.session_state.pending_tab_switch = None


def _is_streaming(prefix: str) -> bool:
    active = st.session_state.get("active_stream")
    return bool(active and str(active.get("kind", "")).startswith(prefix))


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
            "Document persisted</span>",
            unsafe_allow_html=True,
        )
        st.markdown("#### Approved document")
        st.json(result.get("approved_document"))
        return

    if pending_hitl and draft:
        st.markdown(
            '<span class="status-pill pending">'
            '<span class="material-symbols-rounded" style="font-size:14px;">rate_review</span>'
            "Draft ready — your review is required</span>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "I prepared a schema document draft. "
            "Review the JSON and decide whether to **approve**, **edit**, or **reject**."
        )
        with st.expander("View full draft (JSON)", expanded=False):
            st.json(draft)
        return

    if status == "rejected":
        st.markdown(
            '<span class="status-pill blocked">'
            '<span class="material-symbols-rounded" style="font-size:14px;">cancel</span>'
            "Rejected</span>",
            unsafe_allow_html=True,
        )
        st.write(result.get("error", "Document rejected."))
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
            f"Document {('edited and ' if action == 'edit' else '')}approved</span>",
            unsafe_allow_html=True,
        )
        st.markdown("The document was persisted and is now available to the Query Agent.")
        with st.expander("View approved document", expanded=False):
            st.json(result.get("approved_document"))
    elif status == "rejected":
        st.markdown(
            '<span class="status-pill blocked">'
            '<span class="material-symbols-rounded" style="font-size:14px;">cancel</span>'
            "Rejected</span>",
            unsafe_allow_html=True,
        )
        st.write(result.get("error", "Document rejected."))
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
            "Query executed</span>",
            unsafe_allow_html=True,
        )
    elif status == "blocked_missing_schema":
        st.markdown(
            '<span class="status-pill blocked">'
            '<span class="material-symbols-rounded" style="font-size:14px;">block</span>'
            "Missing approved schema</span>",
            unsafe_allow_html=True,
        )
    elif status == "needs_clarification":
        st.markdown(
            '<span class="status-pill info">'
            '<span class="material-symbols-rounded" style="font-size:14px;">help</span>'
            "Needs clarification</span>",
            unsafe_allow_html=True,
        )
    elif status == "out_of_scope":
        st.markdown(
            '<span class="status-pill info">'
            '<span class="material-symbols-rounded" style="font-size:14px;">info</span>'
            "Out of scope</span>",
            unsafe_allow_html=True,
        )
    elif status == "execution_error":
        st.markdown(
            '<span class="status-pill blocked">'
            '<span class="material-symbols-rounded" style="font-size:14px;">error</span>'
            "SQL execution failed</span>",
            unsafe_allow_html=True,
        )
    elif status == "preferences_updated":
        st.markdown(
            '<span class="status-pill ok">'
            '<span class="material-symbols-rounded" style="font-size:14px;">tune</span>'
            "Preferences updated</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<span class="status-pill info">{status}</span>',
            unsafe_allow_html=True,
        )

    assistant_text = (result.get("assistant_text") or "").strip()
    explanation = (result.get("explanation") or "").strip()
    clarification = (result.get("clarification_question") or "").strip()

    if status in ("ok", "preferences_updated"):
        # Direct LLM reply only; no auto-generated debug line, no clarification echo.
        if assistant_text:
            st.markdown(assistant_text)
    elif status == "out_of_scope":
        if assistant_text:
            st.markdown(assistant_text)
    elif status == "needs_clarification":
        # Show the clarification question directly, no "Suggested clarification:" prefix.
        text = clarification or assistant_text
        if text:
            st.markdown(text)
    else:
        # blocked_missing_schema / execution_error / unknown: keep explanation + any extra text.
        if explanation:
            st.markdown(explanation)
        if assistant_text and assistant_text != explanation:
            st.markdown(assistant_text)
        if clarification and clarification not in {explanation, assistant_text}:
            st.markdown(clarification)

    if result.get("sql_final"):
        st.markdown("#### SQL final")
        st.code(result["sql_final"], language="sql")

    sample = result.get("sample")
    if isinstance(sample, dict):
        rows = sample.get("rows") or []
        columns = sample.get("columns") or []
        st.markdown(f"#### Result sample — {sample.get('row_count', 0)} rows")
        if rows and columns:
            table_rows = [dict(zip(columns, row)) for row in rows]
            st.dataframe(table_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("No rows to display.")


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


def _render_schema_empty_state(*, show_generate_button: bool) -> None:
    st.markdown(
        """
        <div class="chat-empty">
          <div class="icon"><span class="material-symbols-rounded">schema</span></div>
          <h3>Start by documenting your database</h3>
          <p>The Schema Agent inspects PostgreSQL and generates a JSON document
          with tables, columns, and descriptions. You can approve, edit, or reject
          before it is persisted.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not show_generate_button:
        return
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        if st.button(
            "Generate schema documentation",
            type="primary",
            use_container_width=True,
            key="schema_generate_btn",
            icon=":material/bolt:",
        ):
            st.session_state.schema_auto_generate = True
            st.rerun()


def _render_active_schema_summary() -> None:
    """Render the currently-approved schema document on first visit.

    When the user opens the Schema Agent tab and a schema is already
    persisted on disk, we previously showed the generic "Start by
    documenting your database" empty-state with no way to see what was
    already approved. This helper shows the approved document instead:
    a status pill, a per-table expander with descriptions and columns,
    and a collapsed raw-JSON expander for completeness.
    """
    store = _get_schema_docs_store()
    try:
        entry = store.latest()
    except Exception:  # noqa: BLE001
        entry = None
    if not entry:
        return
    version = entry.get("version", "")
    approved_at = entry.get("approved_at", "")
    st.markdown(
        '<span class="status-pill ok">'
        '<span class="material-symbols-rounded" style="font-size:14px;">check_circle</span>'
        f"Active schema · v{version}</span>",
        unsafe_allow_html=True,
    )
    if approved_at:
        st.caption(f"Approved at {approved_at}")
    st.markdown(
        "This is the approved schema currently used by the Query Agent. "
        "Use **Reset schema** above to delete it and generate a new one."
    )

    schema_ctx = store.extract_query_schema_context(entry)
    if schema_ctx:
        st.markdown("#### Tables")
        for table_name, table_info in schema_ctx.items():
            cols = table_info.get("columns") or []
            with st.expander(
                f"{table_name} · {len(cols)} column{'s' if len(cols) != 1 else ''}",
                expanded=False,
            ):
                desc = (table_info.get("description") or "").strip()
                if desc:
                    st.markdown(f"_{desc}_")
                lines: list[str] = []
                for col in cols:
                    cname = col.get("name", "")
                    cdesc = (col.get("description") or "").strip()
                    if cdesc:
                        lines.append(f"- **`{cname}`** — {cdesc}")
                    else:
                        lines.append(f"- **`{cname}`**")
                if lines:
                    st.markdown("\n".join(lines))

    with st.expander("View raw JSON document", expanded=False):
        st.json(entry.get("document"))


def _render_query_empty_state_blocked() -> None:
    """Empty state for Query Agent when no approved schema exists."""
    st.markdown(
        """
        <div class="chat-empty">
          <div class="icon"><span class="material-symbols-rounded">block</span></div>
          <h3>No approved schema yet</h3>
          <p>The Query Agent needs an approved schema document before answering.
          Go to the Schema Agent tab to generate one and unlock NL2SQL chat.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        if st.button(
            "Go to Schema Agent",
            type="primary",
            use_container_width=True,
            key="query_go_to_schema_btn",
            icon=":material/arrow_back:",
        ):
            st.session_state.pending_tab_switch = "schema"
            st.rerun()


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


def _clear_schema_edit_state() -> None:
    """Remove any leftover field-level edit widgets state from previous drafts."""
    for key in list(st.session_state.keys()):
        if isinstance(key, str) and key.startswith("schema_edit_"):
            del st.session_state[key]


def _collect_edited_document(draft: dict[str, Any] | None) -> dict[str, Any]:
    """Read field-level edits from session_state and reconstruct the document.

    The renderer below lays out one input per editable field
    (``table_name``, table ``description``, column ``name`` + ``description``).
    Extra/unknown keys on each table/column are preserved as-is so we don't lose
    data that the HITL UI doesn't surface.
    """
    base = draft if isinstance(draft, dict) else {}
    edited: dict[str, Any] = {k: v for k, v in base.items() if k != "tables"}

    tables_src = base.get("tables") if isinstance(base.get("tables"), list) else []
    edited_tables: list[dict[str, Any]] = []
    for t_idx, table in enumerate(tables_src):
        if not isinstance(table, dict):
            continue
        new_table: dict[str, Any] = {k: v for k, v in table.items() if k not in {"columns"}}
        name_key = f"schema_edit_table_name__{t_idx}"
        desc_key = f"schema_edit_table_desc__{t_idx}"
        new_table["table_name"] = st.session_state.get(
            name_key, table.get("table_name", "")
        )
        new_table["description"] = st.session_state.get(
            desc_key, table.get("description", "")
        )

        columns_src = table.get("columns") if isinstance(table.get("columns"), list) else []
        new_columns: list[dict[str, Any]] = []
        for c_idx, col in enumerate(columns_src):
            if not isinstance(col, dict):
                continue
            new_col: dict[str, Any] = dict(col)
            c_name_key = f"schema_edit_col_name__{t_idx}__{c_idx}"
            c_desc_key = f"schema_edit_col_desc__{t_idx}__{c_idx}"
            new_col["name"] = st.session_state.get(c_name_key, col.get("name", ""))
            new_col["description"] = st.session_state.get(
                c_desc_key, col.get("description", "")
            )
            new_columns.append(new_col)
        new_table["columns"] = new_columns
        edited_tables.append(new_table)
    edited["tables"] = edited_tables
    return edited


def _render_schema_edit_fields(draft: dict[str, Any] | None) -> None:
    """Render per-field editors for the draft document.

    Instead of presenting the raw JSON, each table/column is shown as a form row
    so the user can tweak individual fields (typical HITL use case: refine
    descriptions) without touching the JSON structure.
    """
    tables = []
    if isinstance(draft, dict):
        raw_tables = draft.get("tables")
        if isinstance(raw_tables, list):
            tables = [t for t in raw_tables if isinstance(t, dict)]

    if not tables:
        st.info("The draft has no tables to edit.")
        return

    st.caption(
        f"Editing {len(tables)} table(s). Changes apply only when you confirm below."
    )
    for t_idx, table in enumerate(tables):
        table_name = str(table.get("table_name", f"table_{t_idx}"))
        with st.expander(
            f":material/table: {table_name}",
            expanded=(t_idx == 0),
        ):
            name_col, _ = st.columns([2, 3])
            with name_col:
                st.text_input(
                    "Table name",
                    value=str(table.get("table_name", "")),
                    key=f"schema_edit_table_name__{t_idx}",
                )
            st.text_area(
                "Table description",
                value=str(table.get("description", "")),
                key=f"schema_edit_table_desc__{t_idx}",
                height=72,
            )
            columns = table.get("columns")
            if not isinstance(columns, list) or not columns:
                st.caption("No columns in this table.")
                continue
            st.markdown("**Columns**")
            for c_idx, col in enumerate(columns):
                if not isinstance(col, dict):
                    continue
                c_name_col, c_desc_col = st.columns([1, 3])
                with c_name_col:
                    st.text_input(
                        "Name",
                        value=str(col.get("name", "")),
                        key=f"schema_edit_col_name__{t_idx}__{c_idx}",
                        label_visibility="collapsed" if c_idx > 0 else "visible",
                    )
                with c_desc_col:
                    st.text_input(
                        "Description",
                        value=str(col.get("description", "")),
                        key=f"schema_edit_col_desc__{t_idx}__{c_idx}",
                        label_visibility="collapsed" if c_idx > 0 else "visible",
                    )


def _render_schema_hitl_controls(session_id: str) -> None:
    if not st.session_state.schema_pending_hitl:
        return
    if _is_streaming("schema"):
        # While the HITL resume worker is running, controls are frozen.
        return
    draft = st.session_state.schema_last_draft
    st.markdown("---")
    st.markdown("#### Human review (HITL)")
    action = st.radio(
        "Decision",
        ("approve", "edit", "reject"),
        horizontal=True,
        format_func=lambda x: {
            "approve": "Approve",
            "edit": "Edit fields",
            "reject": "Reject",
        }[x],
        key="schema_hitl_action",
    )
    if action == "edit":
        _render_schema_edit_fields(draft)
    reason = ""
    if action == "reject":
        reason = st.text_input(
            "Reason (optional)", value="", key="schema_hitl_reason"
        )

    confirm = st.button("Confirm decision", type="primary", key="schema_hitl_confirm")
    if not confirm:
        return

    if action == "approve":
        human_feedback: dict[str, Any] = {"action": "approve"}
    elif action == "reject":
        human_feedback = {"action": "reject", "reason": reason or "rejected"}
    else:
        edited_document = _collect_edited_document(draft if isinstance(draft, dict) else {})
        human_feedback = {"action": "edit", "edited_document": edited_document}

    _start_schema_resume(
        session_id=session_id,
        human_feedback=human_feedback,
        action=action,
        reason=reason,
    )
    st.rerun()


def _hitl_user_label(action: str, reason: str) -> str:
    if action == "approve":
        return "HITL decision: **Approve**"
    if action == "edit":
        return "HITL decision: **Edit JSON** (with changes)"
    suffix = f" \u2014 motivo: {reason}" if reason else ""
    return "HITL decision: **Reject**" + suffix


@st.dialog("Reset schema documentation")
def _reset_schema_dialog() -> None:
    """Modal confirmation for destructive schema reset.

    On confirm the persisted ``schema_docs.json`` is cleared immediately (not on
    the next message) so the Query Agent sees the empty-schema state right away.
    """
    has_schema = _has_approved_schema()
    st.markdown(
        "You are about to **delete the approved schema documentation**. "
        "The Query Agent will stop responding until you generate a new one."
    )
    if has_schema:
        st.markdown(
            '<span class="status-pill pending">'
            '<span class="material-symbols-rounded" style="font-size:14px;">warning</span>'
            "This action cannot be undone</span>",
            unsafe_allow_html=True,
        )
    else:
        st.caption("No approved schema is currently persisted — this will only clear the chat.")

    col_cancel, col_confirm = st.columns([1, 1])
    with col_cancel:
        if st.button("Cancel", use_container_width=True, key="schema_reset_cancel"):
            st.rerun()
    with col_confirm:
        if st.button(
            "Delete schema",
            use_container_width=True,
            type="primary",
            key="schema_reset_confirm",
        ):
            try:
                _get_schema_docs_store().clear()
            except Exception as exc:  # noqa: BLE001
                logger.exception("schema_reset_failed")
                st.error(f"Failed to delete schema: {exc}")
                return
            st.session_state.schema_force_reset = False
            st.session_state.schema_pending_hitl = False
            st.session_state.schema_last_result = None
            st.session_state.schema_last_draft = None
            st.session_state.schema_runner = None
            st.session_state.schema_chat = []
            st.session_state.query_chat = []
            _clear_schema_edit_state()
            st.toast("Schema deleted.", icon=":material/delete:")
            st.rerun()


def _reset_schema_context() -> None:
    _reset_schema_dialog()


_DEFAULT_SCHEMA_PROMPT = "Document the public schema for the DVD rental database."


def _start_schema_stream(session_id: str, prompt: str) -> None:
    """Kick off a Schema Agent run in a background worker thread."""
    _append_schema("user", prompt)
    runner = _get_runner()
    reset_schema = bool(st.session_state.schema_force_reset)
    st.session_state.schema_force_reset = False

    def _factory() -> Any:
        return runner.stream_start(
            session_id=session_id,
            user_message=prompt,
            reset_schema=reset_schema,
        )

    _start_stream_worker(
        kind="schema_start",
        generator_factory=_factory,
        labels=SCHEMA_NODE_LABELS,
        title="Running Schema Agent...",
        on_done=_finalize_schema_start,
    )


def _finalize_schema_start(result: dict[str, Any]) -> None:
    if result["cancelled"]:
        _append_schema(
            "assistant",
            {"kind": "error", "message": "Schema generation cancelled by user."},
        )
        return
    if result["error"]:
        _append_schema(
            "assistant",
            {"kind": "error", "message": result["error"]},
        )
        return
    final = _find_final(result["events"])
    if not final:
        _append_schema(
            "assistant",
            {"kind": "error", "message": "No final result received from graph."},
        )
        return
    state = final.get("state") or {}
    draft = _draft_from_result(state)
    pending = bool(state.get("__interrupt__")) or bool(draft)
    st.session_state.schema_last_result = state
    st.session_state.schema_last_draft = draft
    st.session_state.schema_pending_hitl = pending
    _clear_schema_edit_state()
    _append_schema(
        "assistant",
        {
            "kind": "schema_result",
            "result": state,
            "draft": draft,
            "pending_hitl": pending,
        },
    )


def _start_schema_resume(
    *,
    session_id: str,
    human_feedback: dict[str, Any],
    action: str,
    reason: str,
) -> None:
    """Kick off the HITL resume in a background worker thread."""
    _append_schema("user", _hitl_user_label(action, reason))
    runner = _get_runner()

    def _factory() -> Any:
        return runner.stream_resume(
            session_id=session_id, human_feedback=human_feedback
        )

    def _on_done(result: dict[str, Any]) -> None:
        _finalize_schema_resume(result, action=action)

    _start_stream_worker(
        kind="schema_resume",
        generator_factory=_factory,
        labels=SCHEMA_NODE_LABELS,
        title="Applying human decision...",
        on_done=_on_done,
    )


def _finalize_schema_resume(result: dict[str, Any], *, action: str) -> None:
    if result["cancelled"]:
        _append_schema(
            "assistant",
            {"kind": "error", "message": "HITL resume cancelled by user."},
        )
        return
    if result["error"]:
        _append_schema(
            "assistant",
            {"kind": "error", "message": result["error"]},
        )
        return
    final = _find_final(result["events"])
    if not final:
        _append_schema(
            "assistant",
            {"kind": "error", "message": "No final result received from graph."},
        )
        return
    state = final.get("state") or {}
    st.session_state.schema_last_result = state
    st.session_state.schema_pending_hitl = False
    st.session_state.schema_last_draft = None
    _append_schema(
        "assistant",
        {"kind": "schema_hitl_done", "action": action, "result": state},
    )


def _render_schema_tab(session_id: str) -> None:
    _render_agent_header(
        icon="schema",
        title="Schema Agent",
        subtitle="Document schema with HITL and persist approved JSON",
        action_label="Reset schema",
        action_key="schema_reset_btn",
        on_action=_reset_schema_context,
        action_help="Delete the approved schema document (confirmation required).",
    )

    has_schema = _has_approved_schema()
    is_empty = not st.session_state.schema_chat
    is_streaming = _is_streaming("schema")
    show_generate_cta = (
        is_empty
        and not has_schema
        and not st.session_state.schema_pending_hitl
        and not is_streaming
    )

    chat_area = st.container(height=_chat_area_height(), border=False)
    with chat_area:
        st.markdown('<div class="chat-scroll">', unsafe_allow_html=True)
        if is_empty and not is_streaming:
            if has_schema and not st.session_state.schema_pending_hitl:
                _render_active_schema_summary()
            else:
                _render_schema_empty_state(show_generate_button=show_generate_cta)
        else:
            _render_schema_chat_history()
        if is_streaming:
            _render_active_stream_fragment()
        _render_schema_hitl_controls(session_id)
        st.markdown("</div>", unsafe_allow_html=True)

    if is_streaming:
        _render_stop_button(key="schema_stop_btn")
        return

    if bool(st.session_state.schema_auto_generate):
        st.session_state.schema_auto_generate = False
        _start_schema_stream(session_id, _DEFAULT_SCHEMA_PROMPT)
        st.rerun()
        return

    chat_disabled = st.session_state.schema_pending_hitl or (
        not has_schema and show_generate_cta
    )
    placeholder_text = (
        "Use the button above to generate the first schema..."
        if chat_disabled and not st.session_state.schema_pending_hitl
        else "Type an instruction to document the schema..."
    )
    prompt = st.chat_input(
        placeholder_text,
        key="schema_chat_input",
        disabled=chat_disabled,
    )
    if not prompt:
        return
    _start_schema_stream(session_id, prompt)
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
          <h3>Natural language questions \u2192 SQL</h3>
          <p>The Query Agent uses approved schema documentation from Schema Agent
          to plan, validate, and execute read-only SQL against PostgreSQL.</p>
          <div class="hints">
            <span class="hint-pill">How many rows are there in rental?</span>
            <span class="hint-pill">List the first 10 actors</span>
            <span class="hint-pill">Which films have rating PG?</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _clear_query_chat() -> None:
    st.session_state.query_chat = []
    st.rerun()


def _start_query_stream(session_id: str, question: str) -> None:
    """Kick off a Query Agent run in a background worker thread."""
    _append_query("user", question)
    agent = _get_query_agent()

    def _factory() -> Any:
        return agent.stream(question, session_id=session_id)

    _start_stream_worker(
        kind="query",
        generator_factory=_factory,
        labels=QUERY_NODE_LABELS,
        title="Running Query Agent...",
        on_done=_finalize_query_stream,
    )


def _finalize_query_stream(result: dict[str, Any]) -> None:
    if result["cancelled"]:
        _append_query(
            "assistant",
            {"kind": "error", "message": "Query cancelled by user."},
        )
        return
    if result["error"]:
        _append_query(
            "assistant",
            {"kind": "error", "message": result["error"]},
        )
        return
    final = _find_final(result["events"])
    if not final:
        _append_query(
            "assistant",
            {"kind": "error", "message": "No final response received from graph."},
        )
        return
    response = final.get("response") or {}
    _append_query("assistant", {"kind": "query_result", "result": response})


def _render_query_tab(session_id: str) -> None:
    _render_agent_header(
        icon="terminal",
        title="Query Agent",
        subtitle="NL2SQL with planner, critic, and read-only execution against PostgreSQL",
        action_label="Clear chat",
        action_key="query_clear_btn",
        on_action=_clear_query_chat,
        action_help="Clear this agent chat history.",
    )

    has_schema = _has_approved_schema()
    is_streaming = _is_streaming("query")
    chat_area = st.container(height=_chat_area_height(), border=False)
    with chat_area:
        st.markdown('<div class="chat-scroll">', unsafe_allow_html=True)
        if not has_schema and not is_streaming:
            _render_query_empty_state_blocked()
        elif not st.session_state.query_chat and not is_streaming:
            _render_query_empty_state()
        else:
            _render_query_chat_history()
        if is_streaming:
            _render_active_stream_fragment()
        st.markdown("</div>", unsafe_allow_html=True)

    if is_streaming:
        _render_stop_button(key="query_stop_btn")
        return

    question = st.chat_input(
        "Type your natural language query..."
        if has_schema
        else "Generate schema documentation first to unlock queries...",
        key="query_chat_input",
        disabled=not has_schema,
    )
    if not question:
        return
    _start_query_stream(session_id, question)
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

    _render_tab_switch_js()


if __name__ == "__main__":
    main()
