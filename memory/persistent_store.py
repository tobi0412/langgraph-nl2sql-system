"""Memoria persistente (iteracion 1: estructura base)."""


class PersistentStore:
    """Store persistente para preferencias de usuario."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    def set_preferences(self, user_id: str, preferences: dict[str, str]) -> None:
        self._store[user_id] = preferences

    def get_preferences(self, user_id: str) -> dict[str, str]:
        return self._store.get(user_id, {})
