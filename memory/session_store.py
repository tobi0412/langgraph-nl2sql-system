"""Short-term session memory (iteration 1: base)."""


class SessionStore:
    """Session context store."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, str]] = {}

    def set_context(self, session_id: str, context: dict[str, str]) -> None:
        self._sessions[session_id] = context

    def get_context(self, session_id: str) -> dict[str, str]:
        return self._sessions.get(session_id, {})
