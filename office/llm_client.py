"""Thin wrapper around the Anthropic SDK: plain text calls plus a forced
tool-call helper for structured output (used by the Chief's decomposition
step so the result is always machine-parseable)."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, Optional

import anthropic

from . import config

log = logging.getLogger("office.llm_client")

# A plausible-looking task breakdown used by call_tool in mock mode, so the
# whole pipeline (dependency ordering, concurrency, synthesis) can be
# exercised without an API key or any cost.
_MOCK_SUBTASKS = [
    {"id": "T1", "description": "Design the wireframes and user flow.", "role": "ui_ux_designer", "depends_on": []},
    {"id": "T2", "description": "Define system architecture and tech stack.", "role": "solution_architect", "depends_on": []},
    {"id": "T3", "description": "Build the frontend UI components.", "role": "frontend_developer", "depends_on": ["T1"]},
    {"id": "T4", "description": "Build the backend API and data model.", "role": "backend_developer", "depends_on": ["T2"]},
    {"id": "T5", "description": "Review the backend for security issues.", "role": "security_engineer", "depends_on": ["T4"]},
    {"id": "T6", "description": "Draft a test plan covering frontend and backend.", "role": "qa_engineer", "depends_on": ["T3", "T4"]},
    {"id": "T7", "description": "Write on-page copy and an SEO strategy.", "role": "content_marketing_agent", "depends_on": []},
    {"id": "T8", "description": "Propose pricing tiers for this project.", "role": "pricing_proposal_agent", "depends_on": []},
    {"id": "T9", "description": "Identify extra opportunities beyond the ask.", "role": "opportunity_scout", "depends_on": []},
    {"id": "T10", "description": "Analyze competitors and market positioning.", "role": "prospect_analyst", "depends_on": []},
]


def _mock_delay() -> None:
    time.sleep(random.uniform(0.4, 1.2))

_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _with_retries(fn, *, retries: int = config.MAX_RETRIES):
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            last_exc = exc
            if attempt < retries:
                wait = 2 ** attempt
                log.warning(
                    "LLM call failed (attempt %d/%d): %s - retrying in %ds",
                    attempt + 1, retries + 1, exc, wait,
                )
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def call_text(system: str, user: str, *, model: str, max_tokens: int = 4096) -> str:
    """Plain text completion for a single role's turn."""
    if config.is_mock_mode():
        _mock_delay()
        role_hint = system.strip().splitlines()[0][:100]
        return (
            f"[MOCK OUTPUT - no API call made]\n\n"
            f"Role brief: {role_hint}\n\n"
            f"Simulated draft response for:\n{user[:300]}"
        )

    client = get_client()

    def _do() -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    return _with_retries(_do)


def call_tool(
    system: str,
    user: str,
    *,
    model: str,
    tool_name: str,
    tool_description: str,
    input_schema: Dict[str, Any],
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    """Force the model to respond via a single tool call and return its
    parsed input."""
    if config.is_mock_mode():
        _mock_delay()
        if tool_name == "submit_task_breakdown":
            return {"subtasks": _MOCK_SUBTASKS}
        return {}

    client = get_client()
    tool = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": input_schema,
    }

    def _do() -> Dict[str, Any]:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user}],
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool_name:
                return block.input
        raise RuntimeError(f"Model did not call the expected tool '{tool_name}'")

    return _with_retries(_do)
