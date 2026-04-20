"""Query Agent facade built on LangGraph query flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from graph.query_graph import build_query_graph
from settings import settings


@dataclass
class QueryAgentRunner:
    """Runner wrapper for Query Agent LangGraph."""

    _graph: Any = None

    def __post_init__(self) -> None:
        self._graph = build_query_graph()

    def _config(self, session_id: str, *, run_name: str) -> dict[str, Any]:
        """LangSmith tracing config for Query Agent runs."""
        cfg: dict[str, Any] = {}
        if settings.langchain_tracing_v2:
            cfg["run_name"] = run_name
            cfg["tags"] = ["query-agent", "langgraph", "nl2sql-system"]
            cfg["metadata"] = {
                "session_id": session_id,
                "component": "query-agent",
            }
        return cfg

    def run(
        self,
        *,
        question: str,
        session_id: str = "default",
        user_id: str = "default",
    ) -> dict[str, Any]:
        """Execute Query Agent LangGraph and normalize response contract."""
        state = self._graph.invoke(
            {
                "question": (question or "").strip(),
                "session_id": session_id,
                "user_id": user_id,
            },
            self._config(session_id, run_name="query-agent-run"),
        )
        return _state_to_response(state, question=question, session_id=session_id, user_id=user_id)

    def stream(
        self,
        *,
        question: str,
        session_id: str = "default",
        user_id: str = "default",
    ) -> Iterator[dict[str, Any]]:
        """Stream node-level updates and yield a final response dict."""
        try:
            payload = {
                "question": (question or "").strip(),
                "session_id": session_id,
                "user_id": user_id,
            }
            config = self._config(session_id, run_name="query-agent-run")
            final_state: dict[str, Any] = {}
            for chunk in self._graph.stream(payload, config, stream_mode="updates"):
                if not isinstance(chunk, dict):
                    continue
                for node_name, update in chunk.items():
                    if node_name == "__interrupt__":
                        continue
                    yield {"kind": "node", "name": node_name, "update": update}
                    if isinstance(update, dict):
                        final_state.update(update)
            response = _state_to_response(
                final_state,
                question=question,
                session_id=session_id,
                user_id=user_id,
            )
            yield {"kind": "final", "response": response}
        except Exception as exc:  # noqa: BLE001
            yield {"kind": "error", "message": str(exc)}


def _state_to_response(
    state: dict[str, Any],
    *,
    question: str,
    session_id: str,
    user_id: str,
) -> dict[str, Any]:
    sql_final = state.get("sql_candidate") or None
    return {
        "status": state.get("status", "needs_clarification"),
        "session_id": session_id,
        "user_id": user_id,
        "question": question,
        "sql_final": sql_final,
        "sample": state.get("sample"),
        "assistant_text": state.get("assistant_text", ""),
        "explanation": state.get("explanation", ""),
        "limitations": list(state.get("limitations") or []),
        "clarification_question": state.get("clarification_question"),
        "planner": {
            "intent": state.get("intent"),
            "candidate_tables": list(state.get("candidate_tables") or []),
            "candidate_columns": list(state.get("candidate_columns") or []),
            "needs_clarification": bool(state.get("needs_clarification")),
            "clarification_question": state.get("clarification_question"),
        },
        "validator": dict(state.get("validator") or {}),
    }


def _empty_question_response(question: str, session_id: str, user_id: str) -> dict[str, Any]:
    return {
        "status": "needs_clarification",
        "session_id": session_id,
        "user_id": user_id,
        "question": question,
        "sql_final": None,
        "sample": None,
        "assistant_text": "",
        "explanation": "No valid question was received.",
        "limitations": ["Empty question."],
        "clarification_question": "What query would you like to run on the database?",
        "planner": {
            "intent": None,
            "candidate_tables": [],
            "candidate_columns": [],
            "needs_clarification": True,
            "clarification_question": "What query would you like to run on the database?",
        },
        "validator": {},
    }


class QueryAgent:
    """Thin facade for callers expecting simple class API."""

    def __init__(self) -> None:
        self._runner = QueryAgentRunner()

    def run(
        self,
        question: str,
        *,
        session_id: str = "default",
        user_id: str = "default",
    ) -> dict[str, Any]:
        if not (question or "").strip():
            return _empty_question_response(question, session_id, user_id)
        return self._runner.run(question=question, session_id=session_id, user_id=user_id)

    def stream(
        self,
        question: str,
        *,
        session_id: str = "default",
        user_id: str = "default",
    ) -> Iterator[dict[str, Any]]:
        if not (question or "").strip():
            yield {
                "kind": "final",
                "response": _empty_question_response(question, session_id, user_id),
            }
            return
        yield from self._runner.stream(
            question=question, session_id=session_id, user_id=user_id
        )
