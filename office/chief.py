"""Chief Agent orchestration: decompose a mission, run specialist agents in
dependency order (parallel within a level), and synthesize the result."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from . import config, llm_client, roles
from .schema import AgentOutput, Deliverable, SubTask

log = logging.getLogger("office.chief")

# Set by a caller (e.g. the dashboard's Cancel button) to stop dispatching new
# subtasks. Calls already in flight are allowed to finish - there is no safe
# way to abort a request mid-stream and still bill/interpret it correctly.
CancelEvent = Optional[threading.Event]

# Optional hook for observers (e.g. a dashboard) to receive progress events.
# Called as on_event(event_name, data_dict). Never required for normal use.
EventCallback = Optional[Callable[[str, dict], None]]


def _emit(on_event: EventCallback, event: str, **data) -> None:
    if on_event is not None:
        on_event(event, data)

CHIEF_SYSTEM_PROMPT = roles.CHIEF_SYSTEM_PROMPT

def _decompose_schema() -> dict:
    """Built per call so agents disabled in the dashboard are never assignable."""
    return {
        "type": "object",
        "properties": {
            "subtasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Short unique id, e.g. 'T1'"},
                        "description": {"type": "string"},
                        "role": {"type": "string", "enum": roles.enabled_role_ids()},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "ids of subtasks that must complete first",
                        },
                    },
                    "required": ["id", "description", "role"],
                },
            }
        },
        "required": ["subtasks"],
    }


# Kept for backwards compatibility with anything importing the old constant.
DECOMPOSE_TOOL_SCHEMA = _decompose_schema()


def decompose_mission(mission: str, on_event: EventCallback = None) -> List[SubTask]:
    if not roles.enabled_role_ids():
        raise ValueError("At least one agent must be enabled before running a mission.")
    log.info("Decomposing mission into subtasks...")
    _emit(on_event, "decompose_start")
    result = llm_client.call_tool(
        system=CHIEF_SYSTEM_PROMPT,
        user=(
            f"Mission: {mission}\n\n"
            "Break this into subtasks and assign each to exactly one agent "
            "role from the allowed list. Decide sequencing/dependencies "
            "where one role's work depends on another's (e.g. Frontend "
            "Developer depends on UI/UX Designer's wireframes)."
        ),
        model=config.model_for_role("chief"),
        tool_name="submit_task_breakdown",
        tool_description="Submit the mission's task breakdown.",
        input_schema=_decompose_schema(),
    )
    raw_subtasks = result.get("subtasks") if isinstance(result, dict) else None
    if not isinstance(raw_subtasks, list):
        raise ValueError("The Chief returned an invalid task breakdown.")
    try:
        subtasks = [
            SubTask(
                id=str(t["id"]).strip(),
                description=str(t["description"]).strip(),
                role=str(t["role"]),
                depends_on=[str(dep).strip() for dep in t.get("depends_on", [])],
            )
            for t in raw_subtasks
        ]
    except (KeyError, TypeError) as exc:
        raise ValueError("The Chief returned a malformed task breakdown.") from exc
    subtasks = _drop_disabled(subtasks)
    _validate_task_graph(subtasks)
    log.info("Decomposed into %d subtasks", len(subtasks))
    _emit(on_event, "decompose_done", subtasks=subtasks)
    return subtasks


def _drop_disabled(subtasks: List[SubTask]) -> List[SubTask]:
    """Remove subtasks assigned to agents switched off in the dashboard, and
    strip dependencies pointing at the removed work so the graph stays valid."""
    enabled = set(roles.enabled_role_ids())
    kept = [t for t in subtasks if t.role in enabled]
    if len(kept) == len(subtasks):
        return subtasks

    dropped = [t.id for t in subtasks if t.role not in enabled]
    log.info("Skipping %s - assigned to disabled agents", dropped)
    dropped_ids = set(dropped)
    for t in kept:
        # Remove only dependencies that genuinely belonged to disabled work.
        # Unknown ids must survive so graph validation can report them.
        t.depends_on = [d for d in t.depends_on if d not in dropped_ids]
    return kept


def _validate_task_graph(subtasks: List[SubTask]) -> None:
    """Reject malformed dependency graphs before any specialist call is made."""
    if not subtasks:
        raise ValueError("The Chief produced no runnable subtasks.")
    ids = [task.id for task in subtasks]
    if any(not task_id for task_id in ids):
        raise ValueError("Every subtask must have a non-empty id.")
    duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    if duplicates:
        raise ValueError(f"Duplicate subtask id(s): {', '.join(duplicates)}.")
    known = set(ids)
    for task in subtasks:
        if not task.description:
            raise ValueError(f"Subtask {task.id} has no description.")
        if task.role not in roles.enabled_role_ids():
            raise ValueError(f"Subtask {task.id} uses an unavailable role: {task.role}.")
        unknown = [dep for dep in task.depends_on if dep not in known]
        if unknown:
            raise ValueError(
                f"Subtask {task.id} has unknown dependency/dependencies: {', '.join(unknown)}."
            )
        if task.id in task.depends_on:
            raise ValueError(f"Subtask {task.id} cannot depend on itself.")

    incoming = {task.id: set(task.depends_on) for task in subtasks}
    ready = [task_id for task_id, deps in incoming.items() if not deps]
    visited = 0
    while ready:
        completed = ready.pop()
        visited += 1
        for task_id, deps in incoming.items():
            if completed in deps:
                deps.remove(completed)
                if not deps:
                    ready.append(task_id)
    if visited != len(subtasks):
        raise ValueError("The task breakdown contains a dependency cycle.")


def _run_one(
    subtask: SubTask,
    context: Dict[str, AgentOutput],
    on_event: EventCallback = None,
) -> AgentOutput:
    system = roles.role_prompt(subtask.role)
    dep_context = ""
    if subtask.depends_on:
        parts = []
        for dep_id in subtask.depends_on:
            dep = context.get(dep_id)
            if dep and not dep.error:
                parts.append(
                    f"### Output from {dep_id} ({roles.display_name(dep.role)})\n{dep.content}"
                )
        if parts:
            dep_context = "\n\nRelevant prior work to build on:\n" + "\n\n".join(parts)

    user = f"Sub-task: {subtask.description}{dep_context}"
    _emit(on_event, "subtask_start", id=subtask.id, role=subtask.role)
    started = time.time()
    try:
        content = llm_client.call_text(
            system=system,
            user=user,
            model=config.model_for_role(subtask.role),
            max_tokens=config.max_tokens_role(),
        )
        output = AgentOutput(subtask_id=subtask.id, role=subtask.role, content=content)
    except Exception as exc:  # noqa: BLE001 - convert to a draft-friendly failure note
        log.error("Subtask %s (%s) failed: %s", subtask.id, subtask.role, exc)
        output = AgentOutput(subtask_id=subtask.id, role=subtask.role, content="", error=str(exc))
    output.duration_seconds = round(time.time() - started, 2)
    _emit(
        on_event,
        "subtask_done",
        id=subtask.id,
        role=subtask.role,
        error=output.error,
        output=output.content,
        duration_seconds=output.duration_seconds,
    )
    return output


def run_subtasks(
    subtasks: List[SubTask],
    on_event: EventCallback = None,
    cancel_event: CancelEvent = None,
) -> List[AgentOutput]:
    """Run subtasks in dependency order; subtasks whose dependencies are
    already satisfied run concurrently within the same level."""
    done: Dict[str, AgentOutput] = {}
    remaining = list(subtasks)

    while remaining:
        if cancel_event is not None and cancel_event.is_set():
            log.info("Cancelled - not dispatching %d remaining subtask(s)", len(remaining))
            break

        ready = [t for t in remaining if all(d in done for d in t.depends_on)]
        if not ready:
            raise RuntimeError("Task graph became unresolvable during execution.")

        cap = config.max_parallel()
        workers = min(len(ready), cap) if cap > 0 else len(ready)
        log.info("Running %d subtask(s) in parallel: %s", len(ready), [t.id for t in ready])
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(_run_one, t, done, on_event) for t in ready]
            for future in as_completed(futures):
                output = future.result()
                done[output.subtask_id] = output

        remaining = [t for t in remaining if t.id not in done]

    return [done[t.id] for t in subtasks if t.id in done]


def synthesize(
    mission: str, subtasks: List[SubTask], outputs: List[AgentOutput], on_event: EventCallback = None
) -> str:
    log.info("Synthesizing final deliverable...")
    _emit(on_event, "synthesize_start")
    sections = []
    by_id = {o.subtask_id: o for o in outputs}
    for t in subtasks:
        out = by_id.get(t.id)
        if out is None:
            continue
        label = roles.display_name(t.role)
        body = out.content if not out.error else f"[FAILED: {out.error}]"
        sections.append(f"### {label} ({t.id})\n{body}")

    user = (
        f"Mission: {mission}\n\n"
        "Here are the draft outputs from each specialist role:\n\n"
        + "\n\n".join(sections)
        + "\n\nIntegrate these into one coherent, non-redundant deliverable "
        "package. Note any gaps or conflicts between roles' outputs."
    )
    text = llm_client.call_text(
        system=CHIEF_SYSTEM_PROMPT,
        user=user,
        model=config.model_for_role("chief"),
        max_tokens=config.max_tokens_synthesis(),
    )
    _emit(on_event, "synthesize_done")
    return text


def run_mission(
    mission: str,
    on_event: EventCallback = None,
    cancel_event: CancelEvent = None,
) -> Deliverable:
    started = time.time()
    started_at = datetime.now(timezone.utc).isoformat()
    llm_client.reset_usage()
    was_mock = config.is_mock_mode()

    subtasks = decompose_mission(mission, on_event)
    outputs = run_subtasks(subtasks, on_event, cancel_event)

    cancelled = cancel_event is not None and cancel_event.is_set()
    if cancelled:
        # Skip synthesis - it is another billable call the user just asked to stop.
        synthesis_text = (
            "_Mission cancelled before synthesis. The agent drafts above are "
            "whatever completed before the stop._"
        )
        next_actions = "Re-run the mission to produce a synthesized deliverable."
    else:
        synthesis_text = synthesize(mission, subtasks, outputs, on_event)
        next_actions = (
            "Human review of all drafts above; refine copy/design as needed; "
            "run the QA Engineer's test plan; confirm pricing before sending to "
            "the client; deploy only after explicit human approval."
        )

    finished_at = datetime.now(timezone.utc).isoformat()
    return Deliverable(
        mission=mission,
        subtasks=subtasks,
        outputs=outputs,
        synthesis=synthesis_text,
        next_actions=next_actions,
        cancelled=cancelled,
        mock=was_mock,
        duration_seconds=round(time.time() - started, 2),
        usage=llm_client.get_usage(),
        started_at=started_at,
        finished_at=finished_at,
        configuration={
            "default_model": config.default_model(),
            "chief_model": config.model_for_role("chief"),
            "max_parallel": config.max_parallel(),
            "max_retries": config.max_retries(),
            "max_tokens_role": config.max_tokens_role(),
            "max_tokens_synthesis": config.max_tokens_synthesis(),
            "agents": {
                role_id: {
                    "name": roles.display_name(role_id),
                    "model": config.model_for_role(role_id),
                    "enabled": roles.is_enabled(role_id),
                    "prompt": roles.role_prompt(role_id),
                }
                for role_id in roles.ROLE_IDS
            },
        },
    )
