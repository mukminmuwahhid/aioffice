"""Live terminal dashboard for a mission run.

Shows every specialist role's status (queued/running/done/failed) updating
in place as the Chief Agent hands work between them, plus a scrolling log
of each hand-off.

Usage:
    python live_cli.py "Build a business website with a product catalog and contact form."
    python live_cli.py --mock "Build a business website..."   # free - no real API calls
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from datetime import datetime

if sys.platform == "win32":
    # Windows consoles default to a legacy codepage (e.g. cp1252) that can't
    # encode the emoji in the review banner; force UTF-8 regardless of
    # which console/codepage is active.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a mission with a live terminal dashboard.")
    parser.add_argument("mission", nargs="*", help="The mission text.")
    parser.add_argument(
        "--mock", action="store_true",
        help="Simulate the run with canned responses - no real API calls, no cost.",
    )
    return parser.parse_args()


_args = _parse_args()
if _args.mock:
    # Must be set before office.config/llm_client read it.
    os.environ["AI_OFFICE_MOCK"] = "1"

from rich.console import Console  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from office import chief, roles  # noqa: E402
from office.run_store import save_run  # noqa: E402

console = Console()

STATUS_STYLE = {
    "queued": "blue",
    "running": "bold yellow",
    "done": "bold green",
    "failed": "bold red",
}


def build_table(mission: str, subtasks: dict, order: list) -> Table:
    table = Table(title=f"Mission: {mission}", expand=True)
    table.add_column("Task")
    table.add_column("Role")
    table.add_column("Status")
    table.add_column("Depends on")
    table.add_column("Elapsed")

    now = time.time()
    for sid in order:
        t = subtasks[sid]
        role_name = roles.ROLE_DISPLAY_NAMES[t["role"]]
        style = STATUS_STYLE.get(t["status"], "white")
        elapsed = ""
        if t["started_at"]:
            end = t["finished_at"] or now
            elapsed = f"{end - t['started_at']:.1f}s"
        deps = ", ".join(t["depends_on"]) or "-"
        table.add_row(sid, role_name, f"[{style}]{t['status'].upper()}[/{style}]", deps, elapsed)
    return table


def main() -> None:
    mission = " ".join(_args.mission).strip()
    if not mission:
        mission = console.input(
            "[bold cyan]What mission would you like the web development team to carry out?[/]\n> "
        ).strip()
    if not mission:
        console.print("[red]No mission provided.[/]")
        sys.exit(1)

    if _args.mock:
        console.print(Panel(
            "[bold yellow]MOCK MODE[/] - no real API calls will be made, this run is free.",
            border_style="yellow",
        ))

    state = {"subtasks": {}, "order": []}
    lock = threading.Lock()
    live_holder: dict = {}

    def log(msg: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        live_holder["live"].console.print(f"[dim]{timestamp}[/] {msg}")

    def refresh() -> None:
        live_holder["live"].update(build_table(mission, state["subtasks"], state["order"]))

    def on_event(event: str, data: dict) -> None:
        with lock:
            if event == "decompose_start":
                log("[bold]Chief[/] is decomposing the mission into subtasks...")
            elif event == "decompose_done":
                subtasks = data["subtasks"]
                state["order"] = [t.id for t in subtasks]
                state["subtasks"] = {
                    t.id: {
                        "id": t.id, "role": t.role, "description": t.description,
                        "depends_on": t.depends_on, "status": "queued",
                        "started_at": None, "finished_at": None,
                    }
                    for t in subtasks
                }
                log(f"[bold]Chief[/] split the mission into {len(subtasks)} subtasks.")
                refresh()
            elif event == "subtask_start":
                t = state["subtasks"][data["id"]]
                t["status"] = "running"
                t["started_at"] = time.time()
                role_name = roles.ROLE_DISPLAY_NAMES[t["role"]]
                if t["depends_on"]:
                    dep_roles = [roles.ROLE_DISPLAY_NAMES[state["subtasks"][d]["role"]] for d in t["depends_on"]]
                    log(f"[cyan]{role_name}[/] picked up {data['id']}, using work from {', '.join(dep_roles)}")
                else:
                    log(f"[cyan]{role_name}[/] started {data['id']}")
                refresh()
            elif event == "subtask_done":
                t = state["subtasks"][data["id"]]
                t["status"] = "failed" if data.get("error") else "done"
                t["finished_at"] = time.time()
                role_name = roles.ROLE_DISPLAY_NAMES[t["role"]]
                if data.get("error"):
                    log(f"[red]{role_name}[/] FAILED {data['id']}: {data['error']}")
                else:
                    log(f"[green]{role_name}[/] finished {data['id']}, handing off output")
                refresh()
            elif event == "synthesize_start":
                log("[bold]Chief[/] is synthesizing the final deliverable...")

    with Live(build_table(mission, {}, []), console=console, refresh_per_second=6) as live:
        live_holder["live"] = live
        deliverable = chief.run_mission(mission, on_event=on_event)

    run_dir = save_run(deliverable)
    console.print()
    console.print(Panel(deliverable.review_banner, border_style="yellow"))
    console.print(f"[bold]Full deliverable:[/] {run_dir / 'deliverable.md'}")


if __name__ == "__main__":
    main()
