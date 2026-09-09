"""Data model for a Chief Agent mission run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SubTask:
    id: str
    description: str
    role: str
    depends_on: List[str] = field(default_factory=list)


@dataclass
class AgentOutput:
    subtask_id: str
    role: str
    content: str
    error: Optional[str] = None


@dataclass
class Deliverable:
    mission: str
    subtasks: List[SubTask]
    outputs: List[AgentOutput]
    synthesis: str
    next_actions: str
    review_banner: str = "⚠️ Draft only — requires human approval before use."
