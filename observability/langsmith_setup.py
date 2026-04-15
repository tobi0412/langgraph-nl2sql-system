"""LangSmith: traces LLM and LangGraph runs when LANGCHAIN_TRACING_V2 is enabled.

Tool calls:
- Invocations via LangGraph ``ToolNode`` (model requests a tool) appear as tool steps under the graph trace.
- Schema preload uses ``observability.mcp_tracing.run_schema_inspect_for_preload`` so that path is also visible.
"""

from __future__ import annotations

import logging

from settings import settings

logger = logging.getLogger(__name__)


def log_langsmith_status() -> None:
    """Log whether LangSmith tracing is active (requires LANGCHAIN_API_KEY in production)."""
    if settings.langchain_tracing_v2:
        has_key = bool(settings.langchain_api_key.strip())
        logger.info(
            "langsmith_tracing_enabled",
            extra={
                "project": settings.langchain_project or "(default)",
                "api_key_configured": has_key,
            },
        )
        if not has_key:
            logger.warning(
                "langsmith_tracing_on_but_missing_LANGCHAIN_API_KEY",
            )
    else:
        logger.debug("langsmith_tracing_disabled_set_LANGCHAIN_TRACING_V2_true_to_enable")
