"""LangSmith spans for MCP tools when they run outside the LangGraph ToolNode (e.g. schema preload)."""

from __future__ import annotations

from typing import Any

from langsmith import traceable


@traceable(run_type="tool", name="mcp_schema_inspect_preload")
def run_schema_inspect_for_preload(tool: Any) -> dict[str, Any]:
    """
    Wraps ``MCPSchemaInspectTool._run()`` so preload appears as a tool run in LangSmith.

    Calls from inside ``ToolNode`` are traced by LangChain/LangGraph automatically; this
    only covers the eager preload in ``SchemaAgentRunner.start``.
    """
    return tool._run()
