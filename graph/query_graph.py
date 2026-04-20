"""Compile Query Agent LangGraph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.query_edges import (
    route_after_critic,
    route_after_execute,
    route_after_planner,
    route_after_prefs_update,
    route_after_prepare,
)
from graph.query_nodes import (
    critic_node,
    execute_query_node,
    finalize_query_node,
    planner_node,
    preferences_update_node,
    prepare_query_node,
)
from graph.query_state import QueryAgentState


def build_query_graph():
    """Build and compile the Query Agent graph.

    Flow:
        START -> prefs_update -> prepare -> planner -> critic -> execute -> finish
                           \\                    \\       |          |
                            \\__ finish          +------ planner (retry loop)
                                                          ^ up to MAX_PLAN_RETRIES

    ``prefs_update`` runs first so any language/format/date/strictness change
    is persisted before the rest of the pipeline reads preferences. When the
    turn is ONLY a preference directive, the flow short-circuits to finish
    without planner/critic/execute.
    """
    graph = StateGraph(QueryAgentState)
    graph.add_node("prefs_update", preferences_update_node)
    graph.add_node("prepare", prepare_query_node)
    graph.add_node("planner", planner_node)
    graph.add_node("critic", critic_node)
    graph.add_node("execute", execute_query_node)
    graph.add_node("finish", finalize_query_node)

    graph.add_edge(START, "prefs_update")
    graph.add_conditional_edges(
        "prefs_update",
        route_after_prefs_update,
        {"prepare": "prepare", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "prepare",
        route_after_prepare,
        {"planner": "planner", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {"critic": "critic", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "critic",
        route_after_critic,
        {"execute": "execute", "planner": "planner", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "execute",
        route_after_execute,
        {"planner": "planner", "finish": "finish"},
    )
    graph.add_edge("finish", END)
    return graph.compile()
