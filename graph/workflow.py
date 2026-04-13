"""Workflow base de LangGraph para iteracion 1."""

from dataclasses import dataclass


@dataclass
class WorkflowState:
    """Estado minimo del flujo."""

    step: str = "bootstrap"


def build_workflow() -> str:
    """Placeholder de constructor de workflow."""
    return "Workflow base inicializado"
