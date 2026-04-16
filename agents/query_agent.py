"""Query Agent facade built on LangGraph query flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        sql_final = state.get("sql_candidate") or None
        return {
            "status": state.get("status", "needs_clarification"),
            "session_id": session_id,
            "user_id": user_id,
            "question": question,
            "sql_final": sql_final,
            "sample": state.get("sample"),
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
            return {
                "status": "needs_clarification",
                "session_id": session_id,
                "user_id": user_id,
                "question": question,
                "sql_final": None,
                "sample": None,
                "explanation": "No se recibio una pregunta valida.",
                "limitations": ["Pregunta vacia."],
                "clarification_question": "Que consulta queres hacer sobre la base?",
                "planner": {
                    "intent": None,
                    "candidate_tables": [],
                    "candidate_columns": [],
                    "needs_clarification": True,
                    "clarification_question": "Que consulta queres hacer sobre la base?",
                },
                "validator": {},
            }
        return self._runner.run(question=question, session_id=session_id, user_id=user_id)
