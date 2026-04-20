"""Memoria de corto plazo por sesion: JSON + TTL + WorkingMemory (estilo DEMO02-memory).

No confundir con `schema_docs_store.py` (documentacion de schema).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memory.trace import trace_log
from memory.working import WorkingMemory
from settings import settings

logger = logging.getLogger(__name__)

_MAX_CLARIFICATIONS = 20
_MAX_ASSUMPTIONS = 15
_MAX_FILTERS = 12


@dataclass
class SessionSnapshot:
    """Estado de sesion para el Query Agent + buffer tipo working memory."""

    last_question: str = ""
    last_sql: str = ""
    last_status: str = ""
    assumptions: list[str] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)
    recent_filters: list[str] = field(default_factory=list)
    working_messages: list[dict[str, str]] = field(default_factory=list)


def extract_filters_from_sql(sql: str) -> list[str]:
    """Extrae fragmentos simples de condiciones desde la clausula WHERE."""
    if not sql or not sql.strip():
        return []
    m = re.search(
        r"\bwhere\b([\s\S]+?)(?:\bgroup\b|\border\b|\blimit\b|;|\Z)",
        sql,
        flags=re.IGNORECASE,
    )
    if not m:
        return []
    chunk = m.group(1).strip()
    parts = re.split(r"\s+and\s+", chunk, flags=re.IGNORECASE)
    out = [p.strip() for p in parts if p.strip()]
    return out[:_MAX_FILTERS]


class SessionStore:
    """Persistencia por session_id con TTL; integra WorkingMemory (truncado por tokens)."""

    def __init__(
        self,
        path: str | Path | None = None,
        ttl_seconds: int | None = None,
        working_token_limit: int | None = None,
    ) -> None:
        self._path = Path(path or settings.session_memory_path)
        self._ttl = int(
            settings.session_memory_ttl_seconds if ttl_seconds is None else ttl_seconds
        )
        self._working_limit = int(
            settings.working_session_token_limit
            if working_token_limit is None
            else working_token_limit
        )
        self._lock = threading.RLock()

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load_raw(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"version": 1, "sessions": {}}
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "sessions": {}}
        data.setdefault("version", 1)
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        return data

    def _snapshot_from_raw(self, raw: Any) -> SessionSnapshot:
        if not isinstance(raw, dict):
            return SessionSnapshot()
        wm = raw.get("working_messages")
        wlist: list[dict[str, str]] = []
        if isinstance(wm, list):
            for item in wm:
                if isinstance(item, dict) and "role" in item and "content" in item:
                    wlist.append(
                        {"role": str(item["role"]), "content": str(item["content"])}
                    )
        return SessionSnapshot(
            last_question=str(raw.get("last_question") or ""),
            last_sql=str(raw.get("last_sql") or ""),
            last_status=str(raw.get("last_status") or ""),
            assumptions=[str(x) for x in raw.get("assumptions") or [] if isinstance(x, str)][
                -_MAX_ASSUMPTIONS:
            ],
            clarifications=[
                str(x) for x in raw.get("clarifications") or [] if isinstance(x, str)
            ][-_MAX_CLARIFICATIONS:],
            recent_filters=[
                str(x) for x in raw.get("recent_filters") or [] if isinstance(x, str)
            ][-_MAX_FILTERS:],
            working_messages=wlist,
        )

    def _expired(self, raw: Any, now: float) -> bool:
        if self._ttl <= 0:
            return False
        if not isinstance(raw, dict):
            return True
        ts = raw.get("updated_at")
        try:
            age = now - float(ts)
        except (TypeError, ValueError):
            return False
        return age > self._ttl

    def get_snapshot(self, session_id: str) -> SessionSnapshot:
        """Snapshot o vacio si no existe o expiro por TTL."""
        with self._lock:
            data = self._load_raw()
            sessions: dict[str, Any] = data.setdefault("sessions", {})
            raw = sessions.get(session_id)
            now = time.time()
            if raw is None:
                return SessionSnapshot()
            if self._expired(raw, now):
                del sessions[session_id]
                self._atomic_write(data)
                trace_log("WORKING", f"Session TTL expired: {session_id}")
                logger.info("session_memory_expired", extra={"session_id": session_id})
                return SessionSnapshot()
            snap = self._snapshot_from_raw(raw)
            trace_log("WORKING", f"Loaded session snapshot {session_id}: {len(snap.working_messages)} WM msgs")
            return snap

    def record_turn(
        self,
        session_id: str,
        *,
        question: str,
        sql_candidate: str | None,
        status: str,
        clarification_question: str | None,
        candidate_tables: list[str] | None,
        intent: str | None,
        assistant_summary: str | None = None,
    ) -> None:
        """Persiste turno: campos estructurados + par user/assistant en WorkingMemory."""
        with self._lock:
            data = self._load_raw()
            sessions: dict[str, Any] = data.setdefault("sessions", {})
            prev = sessions.get(session_id)
            now_ts = time.time()
            snap = SessionSnapshot()
            if isinstance(prev, dict) and not self._expired(prev, now_ts):
                snap = self._snapshot_from_raw(prev)

            q = (question or "").strip()
            sql = (sql_candidate or "").strip()
            new_sql = sql or snap.last_sql

            wm = WorkingMemory(token_limit=self._working_limit)
            wm.load_messages(snap.working_messages)
            user_line = q
            assistant_line = (assistant_summary or "").strip() or f"[status={status}]"
            wm.add("user", user_line)
            wm.add("assistant", assistant_line)
            working_messages = wm.get_messages()

            assumptions = list(snap.assumptions)
            tables = [t for t in (candidate_tables or []) if isinstance(t, str)]
            if tables:
                line = f"Tablas candidatas en este turno: {', '.join(tables)}"
                if intent:
                    line = f"{line}; intent={intent}"
                if line not in assumptions:
                    assumptions.append(line)
            assumptions = assumptions[-_MAX_ASSUMPTIONS:]

            clarifications = list(snap.clarifications)
            cq = (clarification_question or "").strip()
            if cq and (not clarifications or clarifications[-1] != cq):
                clarifications.append(cq)
            clarifications = clarifications[-_MAX_CLARIFICATIONS:]

            new_filters = extract_filters_from_sql(sql)
            merged_filters: list[str] = []
            seen: set[str] = set()
            for f in snap.recent_filters + new_filters:
                if f not in seen:
                    seen.add(f)
                    merged_filters.append(f)
            merged_filters = merged_filters[-_MAX_FILTERS:]

            entry = {
                "updated_at": time.time(),
                "last_question": q,
                "last_sql": new_sql,
                "assumptions": assumptions,
                "clarifications": clarifications,
                "recent_filters": merged_filters,
                "last_status": status,
                "working_messages": working_messages,
            }
            sessions[session_id] = entry
            self._atomic_write(data)
            trace_log("WORKING", f"Session persisted {session_id} (WM tokens limit={self._working_limit})")
            logger.info(
                "session_memory_persisted",
                extra={"session_id": session_id, "path": str(self._path)},
            )

    def _atomic_write(self, data: dict[str, Any]) -> None:
        self._ensure_parent()
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    def clear_session(self, session_id: str) -> None:
        """Elimina una sesion (tests o administracion)."""
        with self._lock:
            data = self._load_raw()
            sessions = data.setdefault("sessions", {})
            if session_id in sessions:
                del sessions[session_id]
                self._atomic_write(data)
