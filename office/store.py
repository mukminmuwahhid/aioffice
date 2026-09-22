"""Local workspace state: user-editable settings and per-agent overrides.

Persisted as JSON under .office/ so edits made in the dashboard survive a
restart. Code defaults stay the source of truth - a stored value only ever
layers on top of them, and deleting the JSON restores stock behaviour.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List

STORE_DIR = Path(os.environ.get("AI_OFFICE_STORE_DIR", ".office"))
SETTINGS_FILE = STORE_DIR / "settings.json"
ROLES_FILE = STORE_DIR / "roles.json"

# A blank string means "fall back to the env var / built-in default", which
# keeps an untouched install behaving exactly as it did before settings existed.
DEFAULT_SETTINGS: Dict[str, Any] = {
    "default_model": "",
    "chief_model": "",
    "max_retries": 2,
    "max_parallel": 0,  # 0 = one worker per ready subtask
    "mock_mode": False,
    "max_tokens_role": 4096,
    "max_tokens_synthesis": 8192,
}

# Fields a user may override per agent; anything else in a payload is ignored.
ROLE_OVERRIDE_FIELDS = ("name", "prompt", "icon", "accent", "tagline", "model", "enabled")

_lock = threading.Lock()


class ValidationError(ValueError):
    """A dashboard edit contains one or more invalid fields."""

    def __init__(self, errors: Dict[str, str]):
        super().__init__("Invalid workspace configuration.")
        self.errors = errors


def _read_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(fallback)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomically replace a JSON file so interrupted writes cannot corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_model(value: Any, allowed_models: List[str], *, blank_ok: bool = True) -> bool:
    return isinstance(value, str) and (blank_ok and value == "" or value in allowed_models)


def validate_settings(patch: Dict[str, Any], allowed_models: List[str]) -> None:
    errors: Dict[str, str] = {}
    for field in ("default_model", "chief_model"):
        if field in patch and not _validate_model(patch[field], allowed_models):
            errors[field] = "Select a supported model or leave this blank."
    limits = {
        "max_retries": (0, 5),
        "max_parallel": (0, 10),
        "max_tokens_role": (256, 32768),
        "max_tokens_synthesis": (256, 65536),
    }
    for field, (minimum, maximum) in limits.items():
        if field in patch:
            value = patch[field]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                errors[field] = f"Must be an integer from {minimum} to {maximum}."
    if "mock_mode" in patch and not isinstance(patch["mock_mode"], bool):
        errors["mock_mode"] = "Must be true or false."
    if errors:
        raise ValidationError(errors)


def validate_role_override(patch: Dict[str, Any], allowed_models: List[str]) -> None:
    errors: Dict[str, str] = {}
    text_limits = {"name": 80, "icon": 16, "tagline": 180, "prompt": 30000}
    for field, maximum in text_limits.items():
        if field not in patch:
            continue
        value = patch[field]
        if not isinstance(value, str):
            errors[field] = "Must be text."
        elif field in ("name", "prompt") and not value.strip():
            errors[field] = "Cannot be empty."
        elif len(value) > maximum:
            errors[field] = f"Must contain at most {maximum} characters."
    if "accent" in patch:
        value = patch["accent"]
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            errors["accent"] = "Use a six-digit hex colour such as #2ee6a6."
    if "model" in patch and not _validate_model(patch["model"], allowed_models):
        errors["model"] = "Select a supported model or leave this blank."
    if "enabled" in patch and not isinstance(patch["enabled"], bool):
        errors["enabled"] = "Must be true or false."
    if errors:
        raise ValidationError(errors)


def load_settings() -> Dict[str, Any]:
    with _lock:
        stored = _read_json(SETTINGS_FILE, {})
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    return merged


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `patch` into stored settings, ignoring unknown keys."""
    with _lock:
        stored = _read_json(SETTINGS_FILE, {})
        current = dict(DEFAULT_SETTINGS)
        current.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        current.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
        _write_json(SETTINGS_FILE, current)
    return current


def reset_settings() -> Dict[str, Any]:
    with _lock:
        SETTINGS_FILE.unlink(missing_ok=True)
    return dict(DEFAULT_SETTINGS)


def load_role_overrides() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return _read_json(ROLES_FILE, {})


def save_role_override(role_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        overrides = _read_json(ROLES_FILE, {})
        entry = overrides.get(role_id, {})
        entry.update({k: v for k, v in patch.items() if k in ROLE_OVERRIDE_FIELDS})
        overrides[role_id] = entry
        _write_json(ROLES_FILE, overrides)
    return entry


def reset_role_override(role_id: str) -> None:
    with _lock:
        overrides = _read_json(ROLES_FILE, {})
        if overrides.pop(role_id, None) is None:
            return
        _write_json(ROLES_FILE, overrides)
