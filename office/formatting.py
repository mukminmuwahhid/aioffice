"""Render a Deliverable into the Markdown format prompt.txt specifies."""

from __future__ import annotations

from typing import List

from . import roles
from .schema import Deliverable


def render_markdown(d: Deliverable) -> str:
    lines: List[str] = []

    lines.append("# Mission Deliverable\n")

    lines.append("## 1. Mission Summary\n")
    lines.append(d.mission.strip() + "\n")

    lines.append("## 2. Task Breakdown\n")
    for t in d.subtasks:
        dep = f" (depends on: {', '.join(t.depends_on)})" if t.depends_on else ""
        lines.append(f"- **{t.id}** - {roles.display_name(t.role)}: {t.description}{dep}")
    lines.append("")

    lines.append("## 3. Agent Outputs\n")
    by_id = {o.subtask_id: o for o in d.outputs}
    for t in d.subtasks:
        out = by_id.get(t.id)
        lines.append(f"### {roles.display_name(t.role)} - {t.id}\n")
        if out is None:
            lines.append("_Not run._\n")
        elif out.error:
            lines.append(f"_Failed: {out.error}_\n")
        else:
            lines.append(out.content + "\n")

    lines.append("## 4. Synthesized Deliverable\n")
    lines.append(d.synthesis.strip() + "\n")

    lines.append("## 5. Next Actions\n")
    lines.append(d.next_actions.strip() + "\n")

    lines.append("## 6. Review Banner\n")
    lines.append(d.review_banner + "\n")

    return "\n".join(lines)
