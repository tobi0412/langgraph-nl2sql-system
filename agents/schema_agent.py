"""Schema Agent — LangGraph flow with HITL (iteration 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from graph.schema_graph import build_schema_graph
from graph.schema_nodes import create_initial_messages
from graph.schema_state import SchemaAgentState
from memory.schema_docs_store import SchemaDocsStore
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

    def _config(self, session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

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
            preloaded_schema_metadata = self.schema_inspect_tool._run()
        state = create_initial_schema_state(
            session_id=session_id,
            user_message=user_message,
            has_existing_schema=has_existing_schema,
            reset_schema=reset_schema,
            preloaded_schema_metadata=preloaded_schema_metadata,
        )
        return self._graph.invoke(state, self._config(session_id))

    def resume(self, *, session_id: str, human_feedback: dict[str, Any]) -> dict[str, Any]:
        """Resume after interrupt; ``human_feedback`` must include ``action`` (approve/edit/reject)."""
        return self._graph.invoke(Command(resume=human_feedback), self._config(session_id))


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
