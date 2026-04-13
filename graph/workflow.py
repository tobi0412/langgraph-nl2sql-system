"""Base LangGraph workflow for iteration 1."""

from dataclasses import dataclass


@dataclass
class WorkflowState:
    """Minimal workflow state."""

    step: str = "bootstrap"


def build_workflow() -> str:
    """Workflow builder placeholder."""
    return "Base workflow initialized"
