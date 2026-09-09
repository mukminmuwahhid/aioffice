"""CLI entry point for the AI web-dev office.

Usage:
    python cli.py
    python cli.py "Build a business website with a product catalog and contact form."
"""

from __future__ import annotations

import logging
import sys

from office.chief import run_mission
from office.run_store import save_run

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("office.cli")


def main() -> None:
    if len(sys.argv) > 1:
        mission = " ".join(sys.argv[1:]).strip()
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
