"""Configuration: API key + model selection for each agent role."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

# Optional per-role model overrides - e.g. give planning-heavy roles a
# stronger model and lightweight content roles a cheaper/faster one.
# Falls back to DEFAULT_MODEL for any role not listed here.
MODEL_OVERRIDES = {
    "chief": os.environ.get("ANTHROPIC_MODEL_CHIEF", DEFAULT_MODEL),
    "solution_architect": os.environ.get("ANTHROPIC_MODEL_ARCHITECT", DEFAULT_MODEL),
}


def model_for_role(role: str) -> str:
    return MODEL_OVERRIDES.get(role, DEFAULT_MODEL)


RUNS_DIR = Path(os.environ.get("AI_OFFICE_RUNS_DIR", "runs"))

MAX_RETRIES = 2
