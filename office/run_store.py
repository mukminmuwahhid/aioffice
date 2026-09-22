"""Persist and retrieve mission runs: a human-readable Markdown deliverable
plus a machine-readable JSON dump for auditability/reuse."""

from __future__ import annotations

import json
import re
import shutil
import stat
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .formatting import render_markdown
from .schema import Deliverable

# Run ids are directory names we generate, but they arrive back from the
# browser as URL segments - only ever accept this shape.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "mission"


def save_run(deliverable: Deliverable) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    suffix = uuid.uuid4().hex[:6]
    run_dir = config.runs_dir() / f"{timestamp}_{suffix}_{_slugify(deliverable.mission)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    _atomic_write(run_dir / "deliverable.md", render_markdown(deliverable))
    _atomic_write(
        run_dir / "run.json",
        json.dumps(asdict(deliverable), indent=2, ensure_ascii=False),
    )
    return run_dir


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_dir(run_id: str) -> Optional[Path]:
    """Resolve a run id to its directory, rejecting anything that escapes the
    runs directory (the id comes from an HTTP path segment)."""
    if not _RUN_ID_RE.match(run_id):
        return None
    base = config.runs_dir().resolve()
    candidate = (base / run_id).resolve()
    if candidate.parent != base or not candidate.is_dir():
        return None
    return candidate


def list_runs() -> List[Dict[str, Any]]:
    """Summaries of every saved run, newest first."""
    base = config.runs_dir()
    if not base.is_dir():
        return []

    runs: List[Dict[str, Any]] = []
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        if not any(entry.iterdir()):
            continue  # debris from a delete that couldn't remove the folder itself
        summary: Dict[str, Any] = {
            "id": entry.name,
            "mission": entry.name,
            "subtask_count": 0,
            "failed_count": 0,
            "cancelled": False,
            "mock": False,
            "duration_seconds": None,
            "cost_usd": None,
            "saved_at": datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc).isoformat(),
            "readable": False,
        }
        try:
            data = json.loads((entry / "run.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            runs.append(summary)
            continue

        outputs = data.get("outputs") or []
        usage = data.get("usage") or {}
        summary.update(
            {
                "mission": data.get("mission", entry.name),
                "subtask_count": len(data.get("subtasks") or []),
                "failed_count": sum(1 for o in outputs if o.get("error")),
                "cancelled": bool(data.get("cancelled")),
                "mock": bool(data.get("mock")),
                "duration_seconds": data.get("duration_seconds"),
                "cost_usd": usage.get("cost_usd"),
                "readable": True,
            }
        )
        runs.append(summary)

    runs.sort(key=lambda r: r["id"], reverse=True)
    return runs


def load_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Full saved run payload, or None if the id is unknown/unreadable."""
    run_dir = _run_dir(run_id)
    if run_dir is None:
        return None
    try:
        data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    data["id"] = run_id
    data["path"] = str(run_dir)
    return data


def load_run_markdown(run_id: str) -> Optional[str]:
    run_dir = _run_dir(run_id)
    if run_dir is None:
        return None
    try:
        return (run_dir / "deliverable.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def _clear_readonly(path: Path) -> None:
    """Drop the read-only attribute across a tree.

    A folder synced by OneDrive is routinely flagged read-only, and Windows
    then refuses RemoveDirectory - which is what Python's rmdir calls - even
    though the folder is empty and the user owns it.
    """
    for target in (path, *path.rglob("*")):
        try:
            target.chmod(stat.S_IWRITE)
        except OSError:
            pass  # best effort - rmtree will report anything that still blocks


def delete_run(run_id: str, attempts: int = 4) -> bool:
    """Delete a saved run. Raises OSError if the directory can't be removed.

    Retries because a sync client may also hold a transient handle on a
    just-written folder.
    """
    run_dir = _run_dir(run_id)
    if run_dir is None:
        return False

    last_exc: Optional[OSError] = None
    for attempt in range(attempts):
        try:
            _clear_readonly(run_dir)
            shutil.rmtree(run_dir)
            return True
        except OSError as exc:
            last_exc = exc
            if not run_dir.exists():
                return True
            time.sleep(0.3 * (attempt + 1))

    assert last_exc is not None
    raise last_exc
