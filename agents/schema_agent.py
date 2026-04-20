"""Schema Agent — LangGraph flow with HITL (iteration 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from graph.schema_graph import build_schema_graph
from graph.schema_nodes import create_initial_messages
from graph.schema_state import SchemaAgentState
from memory.schema_docs_store import SchemaDocsStore
from observability.mcp_tracing import run_schema_inspect_for_preload
from settings import settings
from tools.mcp_schema_tool import MCPSchemaInspectTool


def create_initial_schema_state(
    *,
    session_id: str,
    user_message: str = "Document the public schema for the DVD rental database.",
    has_existing_schema: bool = False,
    reset_schema: bool = False,
    preloaded_schema_metadata: dict[str, Any] | None = None,
) -> SchemaAgentState:
    """Build initial graph input (messages + limits + ids)."""
    return {
        "messages": create_initial_messages(
            user_message,
            preloaded_schema_metadata=preloaded_schema_metadata,
        ),
        "iteration": 0,
        "max_iterations": settings.schema_agent_max_iterations,
        "session_id": session_id,
        "has_existing_schema": has_existing_schema,
        "reset_schema": reset_schema,
        "preloaded_schema_metadata": preloaded_schema_metadata,
    }


@dataclass
class SchemaAgentRunner:
    """Keeps a single checkpointer so interrupt/resume works on the same thread."""

    checkpointer: BaseCheckpointSaver | None = None
    _graph: Any = None
    schema_docs_store: SchemaDocsStore | None = None
    schema_inspect_tool: MCPSchemaInspectTool | None = None

    def __post_init__(self) -> None:
        if self.checkpointer is None:
            self.checkpointer = MemorySaver()
        if self.schema_docs_store is None:
            self.schema_docs_store = SchemaDocsStore()
        if self.schema_inspect_tool is None:
            self.schema_inspect_tool = MCPSchemaInspectTool()
        self._graph = build_schema_graph(checkpointer=self.checkpointer)

    @property
    def graph(self):
        return self._graph

    def _config(self, session_id: str, *, run_name: str) -> dict[str, Any]:
        cfg: dict[str, Any] = {"configurable": {"thread_id": session_id}}
        if settings.langchain_tracing_v2:
            cfg["run_name"] = run_name
            cfg["tags"] = ["schema-agent", "langgraph", "nl2sql-system"]
            cfg["metadata"] = {
                "session_id": session_id,
                "component": "schema-agent",
            }
        return cfg

    def start(
        self,
        *,
        session_id: str,
        user_message: str = "Document the public schema for the DVD rental database.",
        reset_schema: bool = False,
    ) -> dict[str, Any]:
        """Run until HITL interrupt or completion."""
        existing_entries = self.schema_docs_store.list_approved()
        has_existing_schema = bool(existing_entries) and not reset_schema
        preloaded_schema_metadata: dict[str, Any] | None = None
        if not has_existing_schema:
            preloaded_schema_metadata = run_schema_inspect_for_preload(self.schema_inspect_tool)
        state = create_initial_schema_state(
            session_id=session_id,
            user_message=user_message,
            has_existing_schema=has_existing_schema,
            reset_schema=reset_schema,
            preloaded_schema_metadata=preloaded_schema_metadata,
        )
        return self._graph.invoke(state, self._config(session_id, run_name="schema-agent-start"))

    def resume(self, *, session_id: str, human_feedback: dict[str, Any]) -> dict[str, Any]:
        """Resume after interrupt; ``human_feedback`` must include ``action`` (approve/edit/reject)."""
        return self._graph.invoke(
            Command(resume=human_feedback),
            self._config(session_id, run_name="schema-agent-resume"),
        )

    def stream_start(
        self,
        *,
        session_id: str,
        user_message: str = "Document the public schema for the DVD rental database.",
        reset_schema: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Stream node-level updates until HITL interrupt or completion.

        Yields dicts with ``kind`` in {"node", "interrupt", "final", "error"}.
        """
        try:
            existing_entries = self.schema_docs_store.list_approved()
            has_existing_schema = bool(existing_entries) and not reset_schema
            preloaded_schema_metadata: dict[str, Any] | None = None
            if not has_existing_schema:
                preloaded_schema_metadata = run_schema_inspect_for_preload(
                    self.schema_inspect_tool
                )
            state = create_initial_schema_state(
                session_id=session_id,
                user_message=user_message,
                has_existing_schema=has_existing_schema,
                reset_schema=reset_schema,
                preloaded_schema_metadata=preloaded_schema_metadata,
            )
            config = self._config(session_id, run_name="schema-agent-start")
            yield from self._stream_graph(state, config)
        except Exception as exc:  # noqa: BLE001
            yield {"kind": "error", "message": str(exc)}

    def stream_resume(
        self,
        *,
        session_id: str,
        human_feedback: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        """Stream node-level updates after HITL resume."""
        try:
            config = self._config(session_id, run_name="schema-agent-resume")
            yield from self._stream_graph(Command(resume=human_feedback), config)
        except Exception as exc:  # noqa: BLE001
            yield {"kind": "error", "message": str(exc)}

    def _stream_graph(self, payload: Any, config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Iterate graph updates, yielding node events and final state snapshot."""
        interrupt_value: Any = None
        for chunk in self._graph.stream(payload, config, stream_mode="updates"):
            if not isinstance(chunk, dict):
                continue
            for node_name, update in chunk.items():
                if node_name == "__interrupt__":
                    interrupt_value = update
                    continue
                yield {"kind": "node", "name": node_name, "update": update}

        snapshot = self._graph.get_state(config)
        final_state: dict[str, Any] = dict(getattr(snapshot, "values", {}) or {})
        if interrupt_value is not None:
            final_state["__interrupt__"] = interrupt_value
        else:
            tasks = getattr(snapshot, "tasks", None) or ()
            pending = []
            for task in tasks:
                task_interrupts = getattr(task, "interrupts", None) or ()
                pending.extend(task_interrupts)
            if pending:
                final_state["__interrupt__"] = tuple(pending)
        yield {"kind": "final", "state": final_state}


def build_schema_agent_graph(*, checkpointer: BaseCheckpointSaver | None = None):
    """Return compiled Schema Agent graph (see ``graph.schema_graph.build_schema_graph``)."""
    return build_schema_graph(checkpointer=checkpointer)


class SchemaAgent:
    """Thin facade for callers that expect a simple class API."""

    def __init__(self, checkpointer: BaseCheckpointSaver | None = None) -> None:
        self._runner = SchemaAgentRunner(checkpointer=checkpointer)

    def run(self) -> str:
        return "Use SchemaAgentRunner.start() / resume() for the LangGraph flow."


__all__ = [
    "SchemaAgent",
    "SchemaAgentRunner",
    "build_schema_agent_graph",
    "create_initial_schema_state",
]
