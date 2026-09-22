"""Configuration: API key, model selection per role, and cost estimation.

Values resolve in this order: dashboard setting (office/store.py) -> env var
-> built-in default. Everything is a function rather than a module constant so
a settings change in the dashboard takes effect without a restart.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import store

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FALLBACK_MODEL = "claude-sonnet-5"

# USD per 1M tokens (input, output). Cached from the Anthropic pricing
# reference on 2026-06-24 - shown as an estimate in the dashboard, not a bill.
PRICING: Dict[str, Tuple[float, float]] = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Offered in the dashboard's model pickers.
SELECTABLE_MODELS = list(PRICING.keys())


def api_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY")


def default_model() -> str:
    return (
        store.load_settings()["default_model"]
        or os.environ.get("ANTHROPIC_MODEL")
        or FALLBACK_MODEL
    )


def model_for_role(role: str) -> str:
    """Per-role model: agent override -> chief setting -> default model."""
    if role == "chief":
        return store.load_settings()["chief_model"] or default_model()
    override = store.load_role_overrides().get(role, {})
    return override.get("model") or default_model()


def runs_dir() -> Path:
    return Path(os.environ.get("AI_OFFICE_RUNS_DIR", "runs"))


def max_retries() -> int:
    return int(store.load_settings()["max_retries"])


def max_parallel() -> int:
    return int(store.load_settings()["max_parallel"])


def max_tokens_role() -> int:
    return int(store.load_settings()["max_tokens_role"])


def max_tokens_synthesis() -> int:
    return int(store.load_settings()["max_tokens_synthesis"])


def is_mock_mode() -> bool:
    """When true, llm_client returns canned responses instead of calling the
    real Anthropic API - lets the whole pipeline be exercised for free."""
    if os.environ.get("AI_OFFICE_MOCK", "").strip().lower() in ("1", "true", "yes"):
        return True
    return bool(store.load_settings()["mock_mode"])


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """USD estimate for one model's usage, or None if the model isn't priced."""
    rates = PRICING.get(model)
    if rates is None:
        return None
    return (input_tokens / 1_000_000) * rates[0] + (output_tokens / 1_000_000) * rates[1]
