"""Chief Agent orchestration: decompose a mission, run specialist agents in
dependency order (parallel within a level), and synthesize the result."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

from . import config, llm_client, roles
from .schema import AgentOutput, Deliverable, SubTask

log = logging.getLogger("office.chief")

# Optional hook for observers (e.g. a dashboard) to receive progress events.
# Called as on_event(event_name, data_dict). Never required for normal use.
EventCallback = Optional[Callable[[str, dict], None]]


def _emit(on_event: EventCallback, event: str, **data) -> None:
    if on_event is not None:
        on_event(event, data)

CHIEF_SYSTEM_PROMPT = roles.CHIEF_SYSTEM_PROMPT

DECOMPOSE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Short unique id, e.g. 'T1'"},
                    "description": {"type": "string"},
                    "role": {"type": "string", "enum": roles.ROLE_IDS},
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


def decompose_mission(mission: str, on_event: EventCallback = None) -> List[SubTask]:
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
        input_schema=DECOMPOSE_TOOL_SCHEMA,
    )
    subtasks = [
        SubTask(
            id=t["id"],
            description=t["description"],
            role=t["role"],
            depends_on=t.get("depends_on", []),
        )
        for t in result["subtasks"]
    ]
    log.info("Decomposed into %d subtasks", len(subtasks))
    _emit(on_event, "decompose_done", subtasks=subtasks)
    return subtasks


def _run_one(
    subtask: SubTask, context: Dict[str, AgentOutput], on_event: EventCallback = None
) -> AgentOutput:
    system = roles.ROLE_PROMPTS[subtask.role]
    dep_context = ""
    if subtask.depends_on:
        parts = []
        for dep_id in subtask.depends_on:
            dep = context.get(dep_id)
            if dep and not dep.error:
                parts.append(
                    f"### Output from {dep_id} ({roles.ROLE_DISPLAY_NAMES[dep.role]})\n{dep.content}"
                )
        if parts:
            dep_context = "\n\nRelevant prior work to build on:\n" + "\n\n".join(parts)

    user = f"Sub-task: {subtask.description}{dep_context}"
    _emit(on_event, "subtask_start", id=subtask.id, role=subtask.role)
    try:
        content = llm_client.call_text(
            system=system, user=user, model=config.model_for_role(subtask.role)
        )
        output = AgentOutput(subtask_id=subtask.id, role=subtask.role, content=content)
    except Exception as exc:  # noqa: BLE001 - convert to a draft-friendly failure note
        log.error("Subtask %s (%s) failed: %s", subtask.id, subtask.role, exc)
        output = AgentOutput(subtask_id=subtask.id, role=subtask.role, content="", error=str(exc))
    _emit(on_event, "subtask_done", id=subtask.id, role=subtask.role, error=output.error)
    return output


def run_subtasks(subtasks: List[SubTask], on_event: EventCallback = None) -> List[AgentOutput]:
    """Run subtasks in dependency order; subtasks whose dependencies are
    already satisfied run concurrently within the same level."""
    done: Dict[str, AgentOutput] = {}
    remaining = list(subtasks)

    while remaining:
        ready = [t for t in remaining if all(d in done for d in t.depends_on)]
        if not ready:
            log.warning(
                "Unresolvable dependencies among %s; running remaining tasks anyway",
                [t.id for t in remaining],
            )
            ready = remaining

        log.info("Running %d subtask(s) in parallel: %s", len(ready), [t.id for t in ready])
        with ThreadPoolExecutor(max_workers=max(1, len(ready))) as pool:
            futures = [pool.submit(_run_one, t, done, on_event) for t in ready]
            for future in as_completed(futures):
                output = future.result()
                done[output.subtask_id] = output

        remaining = [t for t in remaining if t.id not in done]

    return [done[t.id] for t in subtasks]


def synthesize(
    mission: str, subtasks: List[SubTask], outputs: List[AgentOutput], on_event: EventCallback = None
) -> str:
    log.info("Synthesizing final deliverable...")
    _emit(on_event, "synthesize_start")
    sections = []
    for t in subtasks:
        out = next(o for o in outputs if o.subtask_id == t.id)
        label = roles.ROLE_DISPLAY_NAMES[t.role]
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
        system=CHIEF_SYSTEM_PROMPT, user=user, model=config.model_for_role("chief"), max_tokens=8192
    )
    _emit(on_event, "synthesize_done")
    return text


def run_mission(mission: str, on_event: EventCallback = None) -> Deliverable:
    subtasks = decompose_mission(mission, on_event)
    outputs = run_subtasks(subtasks, on_event)
    synthesis_text = synthesize(mission, subtasks, outputs, on_event)
    next_actions = (
        "Human review of all drafts above; refine copy/design as needed; "
        "run the QA Engineer's test plan; confirm pricing before sending to "
        "the client; deploy only after explicit human approval."
    )
    return Deliverable(
        mission=mission,
        subtasks=subtasks,
        outputs=outputs,
        synthesis=synthesis_text,
        next_actions=next_actions,
    )
