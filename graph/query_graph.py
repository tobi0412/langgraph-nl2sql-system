"""Compile Query Agent LangGraph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph.query_edges import (
    route_after_critic,
    route_after_execute,
    route_after_planner,
    route_after_prepare,
)
from graph.query_nodes import (
    critic_node,
    execute_query_node,
    finalize_query_node,
    planner_node,
    prefs_finalize_node,
    prepare_query_node,
)
from graph.query_state import QueryAgentState


def build_query_graph():
    """Build and compile the Query Agent graph.

    Flow::

        START -> prepare -> planner -> critic -> execute -> finish -> prefs_finalize -> END
                                          |         |
                                          +------ planner (retry loop)
                                                  ^ up to MAX_PLAN_RETRIES

    Preferences are detected and persisted by ``prefs_finalize`` at the
    VERY END of the graph, after the user has already received the data
    answer. This keeps the perceived response latency low: plain data
    questions never pay for an LLM preferences-detection call on the
    critical path, and preference directives buried inside a data
    question take effect on the next turn rather than delaying this
    one. Pure preference commands (e.g. "respondeme siempre en inglés")
    still get a clean confirmation because ``prefs_finalize`` overrides
    the final assistant payload when it detects a pure command.
    """
    graph = StateGraph(QueryAgentState)
    graph.add_node("prepare", prepare_query_node)
    graph.add_node("planner", planner_node)
    graph.add_node("critic", critic_node)
    graph.add_node("execute", execute_query_node)
    graph.add_node("finish", finalize_query_node)
    graph.add_node("prefs_finalize", prefs_finalize_node)

    graph.add_edge(START, "prepare")
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
    graph.add_edge("finish", "prefs_finalize")
    graph.add_edge("prefs_finalize", END)
    return graph.compile()
