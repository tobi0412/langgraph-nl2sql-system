"""Persistent user preferences with a flexible JSON-first model.

Shape (single blob per ``user_id``)::

    {
        "language": "es",                 # reserved, always present
        "instructions": [                 # free-form behavioral directives
            "Use markdown tables for tabular results.",
            "Prefer ISO date format (YYYY-MM-DD).",
            ...
        ]
    }

``language`` is a reserved preference that is always written. Everything
else lives under ``instructions`` as concise, LLM-authored imperatives
(what the user wants the assistant to do every turn).

Capacity model: total preferences (``1 + len(instructions)``) must not
exceed ``MAX_PREFERENCES``. That is, ``instructions`` holds at most
``MAX_INSTRUCTIONS`` entries. When the list is full, adding a new
instruction requires removing another — the preferences LLM is asked to
pick the replacement, and as a defensive fallback the store evicts the
oldest entry (FIFO) if the LLM forgets.

Backends:
- ``postgres`` (default): single ``preferences JSONB`` column; a small
  inline migration folds the legacy v1 columns (``format``,
  ``date_preference``, ``strictness``) into the JSON blob.
- ``json``: a single file keyed by ``user_id``; used by the test suite
  and when Postgres is not available.
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

MAX_PREFERENCES = 20
MAX_INSTRUCTION_LENGTH = 200
MAX_INSTRUCTIONS = MAX_PREFERENCES - 1  # "language" always occupies one slot
RESERVED_KEYS: tuple[str, ...] = ("language",)

DEFAULT_LANGUAGE = "es"
DEFAULT_PREFERENCES: dict[str, Any] = {
    "language": DEFAULT_LANGUAGE,
    "instructions": [],
}


def _get_pg_connection():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(settings.preferences_database_url, row_factory=dict_row)


CREATE_PREFERENCES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id VARCHAR(255) PRIMARY KEY,
    preferences JSONB NOT NULL DEFAULT '{"language":"es","instructions":[]}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _normalize_instruction(text: Any) -> str:
    """Trim and bound a single instruction string; empty => dropped."""
    if not isinstance(text, str):
        return ""
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    return collapsed[:MAX_INSTRUCTION_LENGTH]


def _normalize_language(value: Any) -> str:
    if not isinstance(value, str):
        return DEFAULT_LANGUAGE
    v = value.strip().lower()
    if not v:
        return DEFAULT_LANGUAGE
    return v[:10]


def _normalize_preferences(raw: Any) -> dict[str, Any]:
    """Coerce any stored blob into the canonical ``{language, instructions}``
    shape, enforcing uniqueness and the instruction cap.
    """
    out: dict[str, Any] = {
        "language": DEFAULT_LANGUAGE,
        "instructions": [],
    }
    if not isinstance(raw, dict):
        return out
    out["language"] = _normalize_language(raw.get("language"))
    instructions: list[str] = []
    seen: set[str] = set()
    raw_instr = raw.get("instructions")
    if isinstance(raw_instr, list):
        for item in raw_instr:
            norm = _normalize_instruction(item)
            if not norm:
                continue
            key = norm.lower()
            if key in seen:
                continue
            seen.add(key)
            instructions.append(norm)
            if len(instructions) >= MAX_INSTRUCTIONS:
                break
    out["instructions"] = instructions
    return out


def _apply_update(
    current: dict[str, Any],
    *,
    language: str | None,
    add_instructions: list[str] | None,
    remove_instructions: list[str] | None,
) -> dict[str, Any]:
    """Apply an LLM-style update with capacity enforcement.

    Order of operations:
    1. Update ``language`` if provided.
    2. Drop every instruction listed in ``remove_instructions`` (case-
       insensitive exact match).
    3. Append every new instruction in ``add_instructions`` that is not
       already present.
    4. If the resulting list still exceeds the cap (the LLM asked to add
       more than it removed), evict the oldest entries FIFO until it
       fits — that way the new, more recent instructions always win.
    """
    merged = _normalize_preferences(current)
    if language:
        merged["language"] = _normalize_language(language)

    instructions: list[str] = list(merged["instructions"])

    if remove_instructions:
        remove_keys = {
            _normalize_instruction(s).lower()
            for s in remove_instructions
            if _normalize_instruction(s)
        }
        if remove_keys:
            instructions = [i for i in instructions if i.lower() not in remove_keys]

    if add_instructions:
        existing_keys = {i.lower() for i in instructions}
        for item in add_instructions:
            norm = _normalize_instruction(item)
            if not norm:
                continue
            k = norm.lower()
            if k in existing_keys:
                continue
            instructions.append(norm)
            existing_keys.add(k)

    while len(instructions) > MAX_INSTRUCTIONS:
        instructions.pop(0)

    merged["instructions"] = instructions
    return merged


class PersistentStore:
    """Per-``user_id`` preferences living in Postgres (JSONB) or a JSON file."""

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
        trace_log("EPISODIC", "user_preferences table ensured (JSONB)")

    # --- JSON backend ---

    def _load_json_raw(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"version": 2, "users": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 2, "users": {}}
        if not isinstance(data, dict):
            return {"version": 2, "users": {}}
        data.setdefault("version", 2)
        if not isinstance(data.get("users"), dict):
            data["users"] = {}
        return data

    def _get_json(self, user_id: str) -> dict[str, Any]:
        data = self._load_json_raw()
        raw = data.get("users", {}).get(user_id)
        return _normalize_preferences(raw)

    def _save_json(self, user_id: str, prefs: dict[str, Any]) -> None:
        data = self._load_json_raw()
        data.setdefault("users", {})[user_id] = prefs
        data["version"] = 2
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)
        trace_log("EPISODIC", f"JSON preferences saved for user_id={user_id}")

    # --- Postgres backend ---

    @traceable(name="pg_get_user_preferences", run_type="chain", tags=["postgres", "preferences"])
    def _get_pg(self, user_id: str) -> dict[str, Any]:
        self._ensure_pg_table()
        with _get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT preferences FROM user_preferences WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
        if not row:
            trace_log("EPISODIC", f"No preference row for user_id={user_id} → defaults")
            return _normalize_preferences(None)
        raw = row.get("preferences") if isinstance(row, dict) else None
        # psycopg returns JSONB as dict already; be defensive in case a
        # string sneaks in.
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        return _normalize_preferences(raw)

    @traceable(name="pg_save_user_preferences", run_type="chain", tags=["postgres", "preferences"])
    def _save_pg(self, user_id: str, prefs: dict[str, Any]) -> None:
        self._ensure_pg_table()
        with _get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_preferences (user_id, preferences, updated_at)
                    VALUES (%s, %s::jsonb, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        preferences = EXCLUDED.preferences,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        user_id,
                        json.dumps(prefs, ensure_ascii=False),
                        datetime.now(UTC),
                    ),
                )
            conn.commit()
        trace_log("EPISODIC", f"Upserted preferences for user_id={user_id}")

    # --- Public API ---

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            if self._backend == "json":
                return self._get_json(user_id)
            return self._get_pg(user_id)

    def update_preferences(
        self,
        user_id: str,
        *,
        language: str | None = None,
        add_instructions: list[str] | None = None,
        remove_instructions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply an LLM-style incremental update and persist the result."""
        with self._lock:
            current = self.get_preferences(user_id)
            updated = _apply_update(
                current,
                language=language,
                add_instructions=add_instructions,
                remove_instructions=remove_instructions,
            )
            if self._backend == "json":
                self._save_json(user_id, updated)
            else:
                self._save_pg(user_id, updated)
            logger.info(
                "user_preferences_updated",
                extra={
                    "user_id": user_id,
                    "language": updated["language"],
                    "n_instructions": len(updated["instructions"]),
                },
            )
            return updated

    def set_preferences(self, user_id: str, preferences: dict[str, Any]) -> dict[str, Any]:
        """Replace the entire preferences blob with a pre-built dict."""
        with self._lock:
            blob = _normalize_preferences(preferences)
            if self._backend == "json":
                self._save_json(user_id, blob)
            else:
                self._save_pg(user_id, blob)
            return blob

    # --- Back-compat shim -----------------------------------------------------

    def merge_preferences(self, user_id: str, partial: dict[str, Any]) -> dict[str, Any]:
        """Legacy entry point kept for older call sites and tests.

        Accepts a ``partial`` dict that may carry:
        - ``language``: new language code (string)
        - ``add_instructions`` / ``instructions``: list[str] to append
        - ``remove_instructions``: list[str] to drop

        Unknown keys (the pre-v2 ``format``/``date_preference``/
        ``strictness``) are ignored silently so legacy callers don't
        break during the migration window. New code should call
        :meth:`update_preferences` directly.
        """
        if not isinstance(partial, dict):
            return self.get_preferences(user_id)
        language = partial.get("language") if isinstance(partial.get("language"), str) else None
        add = partial.get("add_instructions") if isinstance(partial.get("add_instructions"), list) else None
        remove = partial.get("remove_instructions") if isinstance(partial.get("remove_instructions"), list) else None
        if add is None and isinstance(partial.get("instructions"), list):
            add = partial.get("instructions")
        return self.update_preferences(
            user_id,
            language=language,
            add_instructions=add,
            remove_instructions=remove,
        )
