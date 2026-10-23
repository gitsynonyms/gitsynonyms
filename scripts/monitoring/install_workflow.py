#!/usr/bin/env python3
"""Install a tracked workflow source into .github/workflows.

Creating or updating a file under .github/workflows requires a token with the
`workflows` permission. The tracked sources live next to this script; this tool
copies one into place and prints the command to publish it.

    python scripts/monitoring/install_workflow.py                 # monitor
    python scripts/monitoring/install_workflow.py proxy-scan      # full scan
    python scripts/monitoring/install_workflow.py all
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WORKFLOWS: dict[str, str] = {
    "monitor": "monitor-whitehouse.yml",
    "proxy-scan": "proxy-scan.yml",
}


def install(name: str) -> Path:
    source = HERE / WORKFLOWS[name]
    target = ROOT / ".github" / "workflows" / WORKFLOWS[name]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "workflow",
        nargs="?",
        default="monitor",
        choices=(*WORKFLOWS, "all"),
        help="workflow source to install (default monitor)",
    )
    args = parser.parse_args(argv)
    names = list(WORKFLOWS) if args.workflow == "all" else [args.workflow]
    targets = [install(name) for name in names]
    for target in targets:
        print(f"installed {target.relative_to(ROOT)}")
    print("publish it with:")
    print(
        "  git add .github/workflows && "
        "git commit -m 'Add the scheduled workflows' && git push"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

