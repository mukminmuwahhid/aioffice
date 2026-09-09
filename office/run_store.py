"""Persist a mission run to disk: a human-readable Markdown deliverable
plus a machine-readable JSON dump for auditability/reuse."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .formatting import render_markdown
from .schema import Deliverable


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "mission"


def save_run(deliverable: Deliverable) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = config.RUNS_DIR / f"{timestamp}_{_slugify(deliverable.mission)}"
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "deliverable.md").write_text(render_markdown(deliverable), encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps(asdict(deliverable), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return run_dir
