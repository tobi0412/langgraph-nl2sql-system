"""Working memory: buffer de sesion con limite de tokens (estilo DEMO02-memory)."""

from __future__ import annotations

import tiktoken

from memory.trace import trace_log

WORKING_MEMORY_TOKEN_LIMIT = 2000


class WorkingMemory:
    """Buffer de mensajes role/content. Trunca por tokens (mas antiguos primero)."""

    def __init__(self, token_limit: int = WORKING_MEMORY_TOKEN_LIMIT) -> None:
        self.token_limit = token_limit
        self._messages: list[dict[str, str]] = []
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def add(self, role: str, content: str) -> None:
        """Agrega un mensaje y trunca si excede el limite."""
        self._messages.append({"role": role, "content": content})
        self._truncate()

    def get_messages(self) -> list[dict[str, str]]:
        """Lista de mensajes para contexto / serializacion."""
        return list(self._messages)

    def load_messages(self, messages: list[dict[str, str]]) -> None:
        """Reemplaza el buffer (hidratacion desde SessionStore)."""
        self._messages = [
            {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
            for m in messages
            if isinstance(m, dict)
        ]
        self._truncate()

    def _count_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    def _truncate(self) -> None:
        total = sum(self._count_tokens(m.get("content", "")) for m in self._messages)
        while total > self.token_limit and len(self._messages) > 1:
            dropped = self._messages.pop(0)
            total -= self._count_tokens(dropped.get("content", ""))
        trace_log(
            "WORKING",
            f"WorkingMemory: {len(self._messages)} mensaje(s), ~{total} tokens",
        )

    def clear(self) -> None:
        """Limpia el buffer (nueva sesion)."""
        self._messages = []
