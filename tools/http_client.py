"""HTTP client for external MCP tools service."""

from __future__ import annotations

from typing import Any

import httpx

from settings import settings


def call_tools_service(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Call tools service endpoint and return parsed JSON."""
    base = settings.mcp_tools_base_url.rstrip("/")
    url = f"{base}{path}"
    timeout = settings.mcp_tools_timeout_seconds
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Tools service response must be a JSON object")
        return data
