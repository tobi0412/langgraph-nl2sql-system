"""Compile the Schema Agent LangGraph (iteration 3).

Uses ``interrupt`` inside ``human_gate`` so persistence never runs before HITL.
Requires a checkpointer (e.g. ``MemorySaver``) for interrupt/resume.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from graph.schema_edges import route_after_schema_agent
from graph.schema_nodes import (
    format_draft_node,
    human_gate_node,
    persist_approved_node,
    schema_agent_node,
    schema_tools_node,
)
from graph.schema_state import SchemaAgentState


def build_schema_graph(*, checkpointer: BaseCheckpointSaver | None = None):
    """Build and compile the Schema Agent graph.

    Nodes: ``agent`` ↔ ``tools`` → ``format_draft`` → ``human_gate`` → ``persist_approved``.

    Human-in-the-loop: ``human_gate`` calls :func:`langgraph.types.interrupt`;
    resume with ``Command(resume={...})`` containing at least ``action``.
    """
    graph = StateGraph(SchemaAgentState)
    graph.add_node("agent", schema_agent_node)
    graph.add_node("tools", schema_tools_node)
    graph.add_node("format_draft", format_draft_node)
    graph.add_node("human_gate", human_gate_node)
    graph.add_node("persist_approved", persist_approved_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        route_after_schema_agent,
        {"tools": "tools", "format_draft": "format_draft"},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("format_draft", "human_gate")
    graph.add_edge("human_gate", "persist_approved")
    graph.add_edge("persist_approved", END)

    cp = checkpointer if checkpointer is not None else MemorySaver()
    return graph.compile(checkpointer=cp)
