"""Local dashboard: run missions from a browser and watch each role's
status update live, instead of only using the CLI.

Usage:
    python webapp.py
    (then open http://127.0.0.1:5000)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from flask import Flask, jsonify, render_template, request

from office import chief, config, roles
from office.run_store import save_run

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("office.webapp")

app = Flask(__name__)

# Desk avatar glyphs per role - decorative only, no bearing on orchestration.
ROLE_ICONS = {
    "solution_architect": "\U0001F3D7",
    "frontend_developer": "\U0001F5A5",
    "backend_developer": "\U0001F5C4",
    "ui_ux_designer": "\U0001F3A8",
    "security_engineer": "\U0001F512",
    "qa_engineer": "\U0001F41E",
    "content_marketing_agent": "\U0001F4E3",
    "pricing_proposal_agent": "\U0001F4B0",
    "opportunity_scout": "\U0001F52D",
    "prospect_analyst": "\U0001F4CA",
}

# Per-role identity accent (the desk's label color) - always constant,
# separate from the live status color (which encodes queued/running/done).
ROLE_ACCENTS = {
    "solution_architect": "#6d8cff",
    "frontend_developer": "#ff6fae",
    "backend_developer": "#34d399",
    "ui_ux_designer": "#b78bff",
    "security_engineer": "#f2b84b",
    "qa_engineer": "#38d6d6",
    "content_marketing_agent": "#ff8a65",
    "pricing_proposal_agent": "#5eb5ff",
    "opportunity_scout": "#c792ff",
    "prospect_analyst": "#7ee787",
}

# Where each role's desk sits on the office floor plan (see templates/index.html).
FLOOR_LAYOUT = [
    ("top1", "solution_architect"),
    ("top2", "frontend_developer"),
    ("top3", "backend_developer"),
    ("left1", "ui_ux_designer"),
    ("left2", "security_engineer"),
    ("right1", "qa_engineer"),
    ("right2", "content_marketing_agent"),
    ("mid1", "pricing_proposal_agent"),
    ("mid2", "opportunity_scout"),
    ("bottom", "prospect_analyst"),
]

# One-line captions shown on each desk's nameplate.
ROLE_TAGLINES = {
    "solution_architect": "Defines system architecture, tech stack, integrations.",
    "frontend_developer": "Builds UI components, responsive layouts, accessibility.",
    "backend_developer": "Designs APIs, databases, server logic, authentication.",
    "ui_ux_designer": "Wireframes, mockups, user flows, usability improvements.",
    "security_engineer": "Reviews for vulnerabilities, compliance, secure coding.",
    "qa_engineer": "Drafts test plans, unit/integration cases, bug checks.",
    "content_marketing_agent": "Drafts copy, SEO strategy, landing page text.",
    "pricing_proposal_agent": "Suggests pricing tiers, packages, business models.",
    "opportunity_scout": "Identifies extra features/extensions for business value.",
    "prospect_analyst": "Drafts competitor analysis, market positioning insights.",
}

_lock = threading.Lock()
_state = {
    "phase": "idle",  # idle | decomposing | running | synthesizing | done | error
    "mission": None,
    "order": [],
    "subtasks": {},  # id -> {id, role, description, depends_on, status, started_at, finished_at}
    "deliverable": None,
    "run_dir": None,
    "error": None,
}


def _reset_state(mission: str) -> None:
    _state.update(
        {
            "phase": "decomposing",
            "mission": mission,
            "order": [],
            "subtasks": {},
            "deliverable": None,
            "run_dir": None,
            "error": None,
        }
    )


def _on_event(event: str, data: dict) -> None:
    with _lock:
        if event == "decompose_start":
            _state["phase"] = "decomposing"
        elif event == "decompose_done":
            subtasks = data["subtasks"]
            _state["order"] = [t.id for t in subtasks]
            _state["subtasks"] = {
                t.id: {
                    "id": t.id,
                    "role": t.role,
                    "description": t.description,
                    "depends_on": t.depends_on,
                    "status": "queued",
                    "started_at": None,
                    "finished_at": None,
                }
                for t in subtasks
            }
            _state["phase"] = "running"
        elif event == "subtask_start":
            _state["subtasks"][data["id"]]["status"] = "running"
            _state["subtasks"][data["id"]]["started_at"] = time.time()
        elif event == "subtask_done":
            _state["subtasks"][data["id"]]["status"] = "failed" if data.get("error") else "done"
            _state["subtasks"][data["id"]]["finished_at"] = time.time()
        elif event == "synthesize_start":
            _state["phase"] = "synthesizing"


def _run_in_background(mission: str) -> None:
    try:
        deliverable = chief.run_mission(mission, on_event=_on_event)
        run_dir = save_run(deliverable)
        with _lock:
            _state["phase"] = "done"
            _state["deliverable"] = {
                "synthesis": deliverable.synthesis,
                "next_actions": deliverable.next_actions,
                "review_banner": deliverable.review_banner,
            }
            _state["run_dir"] = str(run_dir)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the dashboard
        log.error("Mission failed: %s", exc)
        with _lock:
            _state["phase"] = "error"
            _state["error"] = str(exc)


@app.route("/")
def index():
    floor = {
        slot: {
            "id": rid,
            "name": roles.ROLE_DISPLAY_NAMES[rid],
            "icon": ROLE_ICONS[rid],
            "accent": ROLE_ACCENTS[rid],
            "tagline": ROLE_TAGLINES[rid],
        }
        for slot, rid in FLOOR_LAYOUT
    }
    return render_template("index.html", floor=floor)


@app.route("/run", methods=["POST"])
def run():
    with _lock:
        if _state["phase"] in ("decomposing", "running", "synthesizing"):
            return jsonify({"ok": False, "error": "A mission is already running."}), 409
        body = request.get_json(silent=True) or {}
        mission: Optional[str] = body.get("mission", "").strip()
        if not mission:
            return jsonify({"ok": False, "error": "Mission text is required."}), 400
        os.environ["AI_OFFICE_MOCK"] = "1" if body.get("mock") else ""
        _reset_state(mission)

    threading.Thread(target=_run_in_background, args=(mission,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/status")
def status():
    with _lock:
        desks = []
        for rid in roles.ROLE_IDS:
            matches = [t for t in _state["subtasks"].values() if t["role"] == rid]
            status_value = matches[0]["status"] if matches else "standby"
            desks.append(
                {
                    "id": rid,
                    "name": roles.ROLE_DISPLAY_NAMES[rid],
                    "icon": ROLE_ICONS[rid],
                    "status": status_value,
                }
            )

        ordered_subtasks = [_state["subtasks"][sid] for sid in _state["order"]]

        return jsonify(
            {
                "phase": _state["phase"],
                "mission": _state["mission"],
                "desks": desks,
                "subtasks": ordered_subtasks,
                "deliverable": _state["deliverable"],
                "run_dir": _state["run_dir"],
                "error": _state["error"],
                "mock": config.is_mock_mode(),
            }
        )


if __name__ == "__main__":
    # use_reloader=False: an in-memory mission state + background thread would
    # get silently wiped mid-run if the dev-server auto-restarted (this
    # machine's file-watcher fires spuriously on unrelated site-packages files).
    app.run(debug=True, port=5000, use_reloader=False)
