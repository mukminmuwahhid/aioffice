"""CLI entry point for the AI web-dev office.

Usage:
    python cli.py
    python cli.py "Build a business website with a product catalog and contact form."
    python cli.py --mock "..."   # free - no real API calls
"""

from __future__ import annotations

import logging
import os
import sys

if sys.platform == "win32":
    # Windows consoles default to a legacy codepage (e.g. cp1252) that can't
    # encode the emoji in the review banner; force UTF-8 regardless of
    # which console/codepage is active.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("office.cli")


def main() -> None:
    args = sys.argv[1:]
    if "--mock" in args:
        os.environ["AI_OFFICE_MOCK"] = "1"
        args = [a for a in args if a != "--mock"]

    from office.chief import run_mission
    from office.run_store import save_run

    if args:
        mission = " ".join(args).strip()
    else:
        mission = input(
            "What mission would you like the web development team to carry out?\n> "
        ).strip()

    if not mission:
        log.error("No mission provided.")
        sys.exit(1)

    deliverable = run_mission(mission)
    run_dir = save_run(deliverable)

    log.info("Done. Deliverable written to %s", run_dir / "deliverable.md")
    print(f"\n{deliverable.review_banner}")
    print(f"Full deliverable: {run_dir / 'deliverable.md'}")


if __name__ == "__main__":
    main()
