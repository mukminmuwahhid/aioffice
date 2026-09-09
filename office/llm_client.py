"""Thin wrapper around the Anthropic SDK: plain text calls plus a forced
tool-call helper for structured output (used by the Chief's decomposition
step so the result is always machine-parseable)."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import anthropic

from . import config

log = logging.getLogger("office.llm_client")

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
