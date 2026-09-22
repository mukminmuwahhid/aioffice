"""Data model for a Chief Agent mission run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    duration_seconds: Optional[float] = None


@dataclass
class Deliverable:
    mission: str
    subtasks: List[SubTask]
    outputs: List[AgentOutput]
    synthesis: str
    next_actions: str
    review_banner: str = "⚠️ Draft only — requires human approval before use."
    cancelled: bool = False
    mock: bool = False
    duration_seconds: Optional[float] = None
    usage: Optional[Dict[str, Any]] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    configuration: Dict[str, Any] = field(default_factory=dict)
