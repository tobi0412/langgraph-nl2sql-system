"""MemoryTrace logger — estilo DEMO02-memory (CoALA pillars)."""

import logging

TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace_method(self, message: str, *args, **kwargs) -> None:
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


logging.Logger.trace = _trace_method  # type: ignore[attr-defined]

MEMORY_LOGGER = logging.getLogger("memory.trace")
MEMORY_LOGGER.setLevel(TRACE)
if not MEMORY_LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setLevel(TRACE)
    _h.setFormatter(logging.Formatter("%(message)s"))
    MEMORY_LOGGER.addHandler(_h)

PILLARS = ("WORKING", "EPISODIC", "SEMANTIC", "PROCEDURAL")


def trace_log(pillar: str, message: str) -> None:
    """Emite una linea [PILLAR] para trazas de memoria."""
    if pillar.upper() not in PILLARS:
        pillar = "PROCEDURAL"
    line = f"[{pillar.upper()}]  → {message}"
    MEMORY_LOGGER.log(TRACE, line)
