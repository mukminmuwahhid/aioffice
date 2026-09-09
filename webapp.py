"""Local dashboard: run missions from a browser and watch each role's
status update live, instead of only using the CLI.

Usage:
    python webapp.py
    (then open http://127.0.0.1:5000)
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from flask import Flask, jsonify, render_template, request

from office import chief, roles
from office.run_store import save_run

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("office.webapp")

app = Flask(__name__)

_lock = threading.Lock()
_state = {
    "phase": "idle",  # idle | decomposing | running | synthesizing | done | error
    "mission": None,
    "order": [],
    "subtasks": {},  # id -> {id, role, description, depends_on, status}
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
                }
                for t in subtasks
            }
            _state["phase"] = "running"
        elif event == "subtask_start":
            _state["subtasks"][data["id"]]["status"] = "running"
        elif event == "subtask_done":
            _state["subtasks"][data["id"]]["status"] = "failed" if data.get("error") else "done"
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
    role_list = [
        {"id": rid, "name": roles.ROLE_DISPLAY_NAMES[rid]} for rid in roles.ROLE_IDS
    ]
    return render_template("index.html", roles=role_list)


@app.route("/run", methods=["POST"])
def run():
    with _lock:
        if _state["phase"] in ("decomposing", "running", "synthesizing"):
            return jsonify({"ok": False, "error": "A mission is already running."}), 409
        mission: Optional[str] = (request.get_json(silent=True) or {}).get("mission", "").strip()
        if not mission:
            return jsonify({"ok": False, "error": "Mission text is required."}), 400
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
            desks.append({"id": rid, "name": roles.ROLE_DISPLAY_NAMES[rid], "status": status_value})

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
            }
        )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
