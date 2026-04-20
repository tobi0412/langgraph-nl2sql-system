"""Preferencias persistentes entre sesiones.

Backend `postgres` (estilo DEMO02-memory / episodic: tabla en PostgreSQL) o `json` (archivo).
Independiente de `schema_docs_store.py`.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langsmith import traceable

from memory.trace import trace_log
from settings import settings

logger = logging.getLogger(__name__)

PREFERENCE_KEYS = ("language", "format", "date_preference", "strictness")

DEFAULT_PREFERENCES: dict[str, str] = {
    "language": "es",
    "format": "markdown",
    "date_preference": "iso",
    "strictness": "normal",
}


def _get_pg_connection():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(settings.preferences_database_url, row_factory=dict_row)


CREATE_PREFERENCES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id VARCHAR(255) PRIMARY KEY,
    language VARCHAR(64) NOT NULL DEFAULT 'es',
    format VARCHAR(64) NOT NULL DEFAULT 'markdown',
    date_preference VARCHAR(64) NOT NULL DEFAULT 'iso',
    strictness VARCHAR(64) NOT NULL DEFAULT 'normal',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class PersistentStore:
    """Preferencias por `user_id`: PostgreSQL (por defecto) o JSON."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        backend: str | None = None,
    ) -> None:
        self._path = Path(path or settings.user_preferences_path)
        self._backend = (backend or settings.preferences_store_backend).lower().strip()
        self._lock = threading.RLock()
        self._table_ready = False

    def _ensure_pg_table(self) -> None:
        if self._table_ready:
            return
        with _get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(CREATE_PREFERENCES_TABLE_SQL)
            conn.commit()
        self._table_ready = True
        trace_log("EPISODIC", "user_preferences table ensured")

    def _normalize_user_prefs(self, raw: Any) -> dict[str, str]:
        base = dict(DEFAULT_PREFERENCES)
        if isinstance(raw, dict):
            for key in PREFERENCE_KEYS:
                if key in raw and raw[key] is not None:
                    base[key] = str(raw[key])
        return base

    # --- JSON backend (tests / sin Postgres) ---

    def _load_json_raw(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"version": 1, "users": {}}
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "users": {}}
        data.setdefault("version", 1)
        if not isinstance(data.get("users"), dict):
            data["users"] = {}
        return data

    def _get_json(self, user_id: str) -> dict[str, str]:
        data = self._load_json_raw()
        raw = data.get("users", {}).get(user_id)
        return self._normalize_user_prefs(raw)

    def _merge_json(self, user_id: str, partial: dict[str, str]) -> dict[str, str]:
        data = self._load_json_raw()
        users: dict[str, Any] = data.setdefault("users", {})
        current = self._normalize_user_prefs(users.get(user_id))
        for key, val in partial.items():
            if key in PREFERENCE_KEYS and val is not None:
                current[key] = str(val)
        users[user_id] = current
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)
        trace_log("EPISODIC", f"JSON preferences saved for user_id={user_id}")
        logger.info("user_preferences_json", extra={"user_id": user_id, "path": str(self._path)})
        return dict(current)

    # --- PostgreSQL backend ---

    @traceable(name="pg_get_user_preferences", run_type="chain", tags=["postgres", "preferences"])
    def _get_pg(self, user_id: str) -> dict[str, str]:
        self._ensure_pg_table()
        with _get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT language, format, date_preference, strictness
                    FROM user_preferences
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
        if not row:
            trace_log("EPISODIC", f"No preference row for user_id={user_id} → defaults")
            return dict(DEFAULT_PREFERENCES)
        raw = dict(row)
        out = self._normalize_user_prefs(raw)
        trace_log("EPISODIC", f"Loaded preferences for user_id={user_id}")
        return out

    @traceable(name="pg_merge_user_preferences", run_type="chain", tags=["postgres", "preferences"])
    def _merge_pg(self, user_id: str, partial: dict[str, str]) -> dict[str, str]:
        self._ensure_pg_table()
        current = self._get_pg(user_id)
        for key, val in partial.items():
            if key in PREFERENCE_KEYS and val is not None:
                current[key] = str(val)
        with _get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_preferences (
                        user_id, language, format, date_preference, strictness, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        language = EXCLUDED.language,
                        format = EXCLUDED.format,
                        date_preference = EXCLUDED.date_preference,
                        strictness = EXCLUDED.strictness,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        user_id,
                        current["language"],
                        current["format"],
                        current["date_preference"],
                        current["strictness"],
                        datetime.now(UTC),
                    ),
                )
            conn.commit()
        trace_log("EPISODIC", f"Upserted preferences for user_id={user_id}")
        return current

    def get_preferences(self, user_id: str) -> dict[str, str]:
        with self._lock:
            if self._backend == "json":
                return self._get_json(user_id)
            return self._get_pg(user_id)

    def merge_preferences(self, user_id: str, partial: dict[str, str]) -> dict[str, str]:
        with self._lock:
            if self._backend == "json":
                return self._merge_json(user_id, partial)
            return self._merge_pg(user_id, partial)

    def set_preferences(self, user_id: str, preferences: dict[str, str]) -> dict[str, str]:
        merged = dict(DEFAULT_PREFERENCES)
        for key in PREFERENCE_KEYS:
            if key in preferences and preferences[key] is not None:
                merged[key] = str(preferences[key])
        return self.merge_preferences(user_id, merged)
