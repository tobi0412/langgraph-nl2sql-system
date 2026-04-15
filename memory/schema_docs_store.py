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

    def list_approved(self) -> list[dict[str, Any]]:
        data = self.load_raw()
        entries: list[dict[str, Any]] = data.get("entries", [])
        return list(entries)

    def latest(self) -> dict[str, Any] | None:
        """Return latest approved schema entry for single-user mode."""
        entries = self.list_approved()
        if not entries:
            return None
        return max(entries, key=lambda e: int(e.get("version", 0)))

    def save_approved(
        self,
        *,
        session_id: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        """Upsert current approved schema (single-user). Returns stored entry."""
        self._ensure_parent()
        data = self.load_raw()
        entries: list[dict[str, Any]] = data["entries"]
        current = self.latest()
        version = 1 + int(current.get("version", 0)) if current else 1
        entry = {
            "session_id": session_id,
            "version": version,
            "approved_at": datetime.now(tz=UTC).isoformat(),
            "document": document,
        }
        data["entries"] = []
        data["entries"].append(entry)
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(
            "schema_docs_persisted",
            extra={
                "session_id": session_id,
                "version": version,
                "path": str(self._path),
            },
        )
        return entry
