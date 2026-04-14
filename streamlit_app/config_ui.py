"""Configuracion de la UI desde variables de entorno (spec-ui.md)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def get_api_base_url() -> str:
    """Base URL del API FastAPI (health, futuros endpoints)."""
    return os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")


def get_api_timeout() -> float:
    return float(os.getenv("API_TIMEOUT", "15"))


def get_stream_default() -> bool:
    return os.getenv("STREAM_DEFAULT", "false").lower() in ("1", "true", "yes")
