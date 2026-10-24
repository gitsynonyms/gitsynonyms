#!/usr/bin/env python3
"""Install a tracked workflow source into .github/workflows.

Creating or updating a file under .github/workflows requires a token with the
`workflows` permission. The tracked sources live next to this script (or in
`.github/workflows-deploy/` for the archived templates); this tool copies one
into place, refuses to silently overwrite an active workflow that has moved on,
and prints the command to publish it.

    python scripts/monitoring/install_workflow.py                       # monitor
    python scripts/monitoring/install_workflow.py proxy-scan            # full scan
    python scripts/monitoring/install_workflow.py process-single-video  # end-to-end
    python scripts/monitoring/install_workflow.py all

``all`` only installs the settled sources (monitor, proxy-scan). The archived
end-to-end template must be named explicitly, because installing it overwrites
an active workflow on purpose and asks for review first.
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# name -> (tracked source relative to the repository root, active workflow name)
WORKFLOWS: dict[str, tuple[str, str]] = {
    "monitor": ("scripts/monitoring/monitor-whitehouse.yml", "monitor-whitehouse.yml"),
    "proxy-scan": ("scripts/monitoring/proxy-scan.yml", "proxy-scan.yml"),
    "process-single-video": (
        ".github/workflows-deploy/process-single-video.yml",
        "process-single-video.yml",
    ),
}


# ``all`` stays backward compatible: it never touches a workflow whose tracked
# source is an intentional, still-unmerged change.
SETTLED = ("monitor", "proxy-scan")


class DriftError(RuntimeError):
    """The active workflow changed since the tracked source was cut."""


def source_path(name: str) -> Path:
    return ROOT / WORKFLOWS[name][0]


def target_path(name: str) -> Path:
    return ROOT / ".github" / "workflows" / WORKFLOWS[name][1]


def drift_summary(source: Path, target: Path, limit: int = 40) -> str:
    """Return a short unified diff between the active workflow and the source."""
    active = target.read_text(encoding="utf-8").splitlines(keepends=True)
    tracked = source.read_text(encoding="utf-8").splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            active,
            tracked,
            fromfile=str(target),
            tofile=str(source),
            n=1,
        )
    )
    head = "".join(diff[:limit]).rstrip()
    if len(diff) > limit:
        head += f"\n... ({len(diff) - limit} more diff lines)"
    return head


def install(name: str, *, force: bool = False) -> Path:
    source = source_path(name)
    target = target_path(name)
    if not source.is_file():
        raise FileNotFoundError(f"tracked workflow source is missing: {source}")
    if target.is_file() and target.read_bytes() != source.read_bytes() and not force:
        raise DriftError(
            f"{target.relative_to(ROOT)} differs from the tracked source "
            f"{source.relative_to(ROOT)}:\n{drift_summary(source, target)}\n"
            "Re-cut the source from the active workflow, or pass --force to overwrite it."
        )
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an active workflow that differs from the tracked source",
    )
    args = parser.parse_args(argv)
    names = list(SETTLED) if args.workflow == "all" else [args.workflow]
    try:
        targets = [install(name, force=args.force) for name in names]
    except (DriftError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1
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

