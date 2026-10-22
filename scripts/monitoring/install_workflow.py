#!/usr/bin/env python3
"""Install the White House monitor workflow into .github/workflows.

The scheduled monitor needs a file under .github/workflows, and creating one
requires a token with the `workflows` permission. This script copies the
tracked source file into place and prints the command to publish it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent / "monitor-whitehouse.yml"
TARGET = ROOT / ".github" / "workflows" / "monitor-whitehouse.yml"


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, TARGET)
    print(f"installed {TARGET.relative_to(ROOT)}")
    print("publish it with:")
    print(
        "  git add .github/workflows/monitor-whitehouse.yml && "
        "git commit -m 'Add the White House video monitor workflow' && git push"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
