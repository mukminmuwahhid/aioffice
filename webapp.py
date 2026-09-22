"""Local dashboard for the AI office.

Serves a single-page workspace with four views - the office floor (live
mission control), run history, the agent editor, and settings - over a small
JSON API. State for the in-flight mission is held in memory; everything the
user edits is persisted by office/store.py.

Usage:
    python webapp.py
    (then open http://127.0.0.1:5000)
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import time
from typing import Any, Dict, Optional

from flask import Flask, jsonify, render_template, request

from office import chief, config, llm_client, roles, run_store, store

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("office.webapp")

app = Flask(__name__)

# Where each agent's desk sits on the office floor plan.
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

ACTIVE_PHASES = ("decomposing", "running", "synthesizing")

_lock = threading.Lock()
_cancel_event: Optional[threading.Event] = None
_state: Dict[str, Any] = {
    "phase": "idle",  # idle | decomposing | running | synthesizing | done | cancelled | error
    "mission": None,
    "order": [],
    "subtasks": {},
    "deliverable": None,
    "run_dir": None,
    "run_id": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
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
            "run_id": None,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
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
                    "output": None,
                    "error": None,
                }
                for t in subtasks
            }
            _state["phase"] = "running"
        elif event == "subtask_start":
            entry = _state["subtasks"][data["id"]]
            entry["status"] = "running"
            entry["started_at"] = time.time()
        elif event == "subtask_done":
            entry = _state["subtasks"][data["id"]]
            entry["status"] = "failed" if data.get("error") else "done"
            entry["finished_at"] = time.time()
            entry["error"] = data.get("error")
            entry["output"] = data.get("output")
            entry["duration_seconds"] = data.get("duration_seconds")
        elif event == "synthesize_start":
            _state["phase"] = "synthesizing"


def _run_in_background(mission: str, mock: bool) -> None:
    global _cancel_event
    # Scoped to this run: setting it process-wide would leave every later
    # mission silently mocked after one free run.
    os.environ["AI_OFFICE_MOCK"] = "1" if mock else ""
    try:
        deliverable = chief.run_mission(
            mission, on_event=_on_event, cancel_event=_cancel_event
        )
        run_dir = run_store.save_run(deliverable)
        with _lock:
            # Attach each agent's actual output so the floor is clickable.
            for out in deliverable.outputs:
                entry = _state["subtasks"].get(out.subtask_id)
                if entry is not None:
                    entry["output"] = out.content
                    entry["error"] = out.error
            _state["phase"] = "cancelled" if deliverable.cancelled else "done"
            _state["deliverable"] = {
                "synthesis": deliverable.synthesis,
                "next_actions": deliverable.next_actions,
                "review_banner": deliverable.review_banner,
                "usage": deliverable.usage,
                "duration_seconds": deliverable.duration_seconds,
                "cancelled": deliverable.cancelled,
                "mock": deliverable.mock,
            }
            _state["run_dir"] = str(run_dir)
            _state["run_id"] = run_dir.name
            _state["finished_at"] = time.time()
    except Exception as exc:  # noqa: BLE001 - surface any failure to the dashboard
        log.error("Mission failed: %s", exc)
        with _lock:
            _state["phase"] = "error"
            _state["error"] = str(exc)
            _state["finished_at"] = time.time()
    finally:
        os.environ["AI_OFFICE_MOCK"] = ""


def _floor() -> Dict[str, Dict[str, Any]]:
    return {slot: roles.role_meta(rid) for slot, rid in FLOOR_LAYOUT}


def _json_object():
    body = request.get_json(silent=True)
    if body is None:
        return {}, None
    if not isinstance(body, dict):
        return None, (jsonify({"ok": False, "error": "Request body must be a JSON object."}), 400)
    return body, None


def _configuration_locked():
    with _lock:
        return _state["phase"] in ACTIVE_PHASES


@app.route("/")
def index():
    return render_template("index.html", floor=_floor())


# ---------------------------------------------------------------- mission ---


@app.route("/run", methods=["POST"])
def run():
    global _cancel_event
    with _lock:
        if _state["phase"] in ACTIVE_PHASES:
            return jsonify({"ok": False, "error": "A mission is already running."}), 409
        body, body_error = _json_object()
        if body_error:
            return body_error
        mission = (body.get("mission") or "").strip()
        if not mission:
            return jsonify({"ok": False, "error": "Mission text is required."}), 400
        if not roles.enabled_role_ids():
            return jsonify({"ok": False, "error": "Every agent is disabled."}), 400
        # Per-run toggle; when off, the saved mock_mode setting still applies.
        mock = bool(body.get("mock"))
        _cancel_event = threading.Event()
        _reset_state(mission)

    threading.Thread(target=_run_in_background, args=(mission, mock), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/cancel", methods=["POST"])
def cancel():
    with _lock:
        if _state["phase"] not in ACTIVE_PHASES:
            return jsonify({"ok": False, "error": "No mission is running."}), 409
        if _cancel_event is not None:
            _cancel_event.set()
    return jsonify({"ok": True})


@app.route("/status")
def status():
    with _lock:
        desks = []
        for rid in roles.ROLE_IDS:
            meta = roles.role_meta(rid)
            matches = [t for t in _state["subtasks"].values() if t["role"] == rid]
            priorities = {"failed": 4, "running": 3, "queued": 2, "done": 1}
            representative = max(
                matches,
                key=lambda task: priorities.get(task["status"], 0),
                default=None,
            )
            completed = sum(1 for task in matches if task["status"] == "done")
            failed = sum(1 for task in matches if task["status"] == "failed")
            desks.append(
                {
                    "id": rid,
                    "name": meta["name"],
                    "icon": meta["icon"],
                    "accent": meta["accent"],
                    "tagline": meta["tagline"],
                    "enabled": meta["enabled"],
                    "status": representative["status"] if representative else "standby",
                    "subtask_id": representative["id"] if representative else None,
                    "task_count": len(matches),
                    "completed_count": completed,
                    "failed_count": failed,
                }
            )

        ordered = [_state["subtasks"][sid] for sid in _state["order"]]
        elapsed = None
        if _state["started_at"]:
            end = _state["finished_at"] or time.time()
            elapsed = round(end - _state["started_at"], 1)

        return jsonify(
            {
                "phase": _state["phase"],
                "mission": _state["mission"],
                "desks": desks,
                "subtasks": ordered,
                "deliverable": _state["deliverable"],
                "run_dir": _state["run_dir"],
                "run_id": _state["run_id"],
                "error": _state["error"],
                "mock": (
                    config.is_mock_mode()
                    if _state["phase"] in ACTIVE_PHASES
                    else bool((_state["deliverable"] or {}).get("mock", config.is_mock_mode()))
                ),
                "elapsed_seconds": elapsed,
                "usage": llm_client.get_usage(),
            }
        )


# ------------------------------------------------------------------- runs ---


@app.route("/api/runs")
def api_runs():
    return jsonify({"runs": run_store.list_runs()})


@app.route("/api/runs/<run_id>")
def api_run_detail(run_id: str):
    data = run_store.load_run(run_id)
    if data is None:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    data["display_names"] = {rid: roles.display_name(rid) for rid in roles.ROLE_IDS}
    return jsonify(data)


@app.route("/api/runs/<run_id>/markdown")
def api_run_markdown(run_id: str):
    text = run_store.load_run_markdown(run_id)
    if text is None:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    return jsonify({"markdown": text})


@app.route("/api/runs/<run_id>", methods=["DELETE"])
def api_run_delete(run_id: str):
    try:
        deleted = run_store.delete_run(run_id)
    except OSError as exc:
        log.error("Could not delete run %s: %s", run_id, exc)
        return jsonify(
            {
                "ok": False,
                "error": (
                    "Windows is holding this folder open (often a sync client "
                    "like OneDrive). Close anything reading it and try again."
                ),
            }
        ), 409
    if not deleted:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    return jsonify({"ok": True})


# ----------------------------------------------------------------- agents ---


@app.route("/api/agents")
def api_agents():
    return jsonify(
        {
            "agents": roles.all_role_meta(),
            "models": config.SELECTABLE_MODELS,
            "default_model": config.default_model(),
        }
    )


@app.route("/api/agents/<role_id>", methods=["PUT"])
def api_agent_update(role_id: str):
    if role_id not in roles.ROLE_IDS:
        return jsonify({"ok": False, "error": "Unknown agent."}), 404
    if _configuration_locked():
        return jsonify({"ok": False, "error": "Agents cannot be edited during a mission."}), 409
    body, body_error = _json_object()
    if body_error:
        return body_error
    try:
        store.validate_role_override(body, config.SELECTABLE_MODELS)
        store.save_role_override(role_id, body)
    except store.ValidationError as exc:
        return jsonify({"ok": False, "error": "Invalid agent settings.", "errors": exc.errors}), 400
    return jsonify({"ok": True, "agent": roles.role_meta(role_id)})


@app.route("/api/agents/<role_id>/reset", methods=["POST"])
def api_agent_reset(role_id: str):
    if role_id not in roles.ROLE_IDS:
        return jsonify({"ok": False, "error": "Unknown agent."}), 404
    if _configuration_locked():
        return jsonify({"ok": False, "error": "Agents cannot be reset during a mission."}), 409
    store.reset_role_override(role_id)
    return jsonify({"ok": True, "agent": roles.role_meta(role_id)})


# --------------------------------------------------------------- settings ---


@app.route("/api/settings")
def api_settings():
    return jsonify(
        {
            "settings": store.load_settings(),
            "models": config.SELECTABLE_MODELS,
            "pricing": {m: {"input": p[0], "output": p[1]} for m, p in config.PRICING.items()},
            "effective": {
                "default_model": config.default_model(),
                "chief_model": config.model_for_role("chief"),
                "runs_dir": str(config.runs_dir()),
                "mock_mode": config.is_mock_mode(),
            },
        }
    )


@app.route("/api/settings", methods=["PUT"])
def api_settings_update():
    if _configuration_locked():
        return jsonify({"ok": False, "error": "Settings cannot be changed during a mission."}), 409
    body, body_error = _json_object()
    if body_error:
        return body_error
    try:
        store.validate_settings(body, config.SELECTABLE_MODELS)
        settings = store.save_settings(body)
    except store.ValidationError as exc:
        return jsonify({"ok": False, "error": "Invalid settings.", "errors": exc.errors}), 400
    return jsonify({"ok": True, "settings": settings})


@app.route("/api/settings/reset", methods=["POST"])
def api_settings_reset():
    if _configuration_locked():
        return jsonify({"ok": False, "error": "Settings cannot be reset during a mission."}), 409
    return jsonify({"ok": True, "settings": store.reset_settings()})


@app.route("/api/health")
def api_health():
    enabled = roles.enabled_role_ids()
    return jsonify(
        {
            "api_key_present": bool(config.api_key()),
            "mock_mode": config.is_mock_mode(),
            "default_model": config.default_model(),
            "chief_model": config.model_for_role("chief"),
            "enabled_agents": len(enabled),
            "total_agents": len(roles.ROLE_IDS),
            "runs_dir": str(config.runs_dir().resolve()),
            "store_dir": str(store.STORE_DIR.resolve()),
            "run_count": len(run_store.list_runs()),
            "python": platform.python_version(),
            "platform": sys.platform,
        }
    )


if __name__ == "__main__":
    # use_reloader=False: an in-memory mission state + background thread would
    # get silently wiped mid-run if the dev-server auto-restarted (this
    # machine's file-watcher fires spuriously on unrelated site-packages files).
    app.run(debug=False, host="127.0.0.1", port=5000, use_reloader=False)
