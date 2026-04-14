"""Cliente HTTP hacia el API FastAPI (health; chat opcional futuro)."""

from __future__ import annotations

import httpx


def health_check(base_url: str, *, timeout: float = 5.0) -> tuple[bool, str]:
    """
    GET {base_url}/health.
    Retorna (ok, mensaje). Si falla la red, mensaje describe el error.
    """
    url = f"{base_url.rstrip('/')}/health"
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url)
    except httpx.RequestError as exc:
        return False, f"No se pudo conectar a {url}: {exc}"
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}: {response.text[:500]}"
    try:
        data = response.json()
    except Exception:
        return True, response.text[:200]
    status = data.get("status", "")
    if status == "healthy":
        return True, "API disponible (healthy)"
    return True, str(data)
