"""Persistent storage for approved schema documentation (iteration 3)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from settings import settings

logger = logging.getLogger(__name__)


class SchemaDocsStore:
    """JSON file store for approved schema docs (separate from user preferences)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path or settings.schema_docs_path)

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load_raw(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"entries": []}
        text = self._path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return {"entries": []}
        data.setdefault("entries", [])
        return data

    def list_approved(self, user_id: str | None = None) -> list[dict[str, Any]]:
        data = self.load_raw()
        entries: list[dict[str, Any]] = data.get("entries", [])
        if user_id is None:
            return list(entries)
        return [e for e in entries if e.get("user_id") == user_id]

    def save_approved(
        self,
        *,
        user_id: str,
        session_id: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        """Append an approved snapshot. Returns the stored entry."""
        self._ensure_parent()
        data = self.load_raw()
        entries: list[dict[str, Any]] = data["entries"]
        version = 1 + max((e.get("version", 0) for e in entries if e.get("user_id") == user_id), default=0)
        entry = {
            "user_id": user_id,
            "session_id": session_id,
            "version": version,
            "approved_at": datetime.now(tz=UTC).isoformat(),
            "document": document,
        }
        entries.append(entry)
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(
            "schema_docs_persisted",
            extra={
                "user_id": user_id,
                "session_id": session_id,
                "version": version,
                "path": str(self._path),
            },
        )
        return entry
