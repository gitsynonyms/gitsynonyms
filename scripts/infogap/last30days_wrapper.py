#!/usr/bin/env python3
"""Wrapper to call last30days skill programmatically."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

# Add the last30days_skill to path
SKILL_DIR = Path(__file__).resolve().parent.parent / "last30days_skill"
sys.path.insert(0, str(SKILL_DIR))

# Import the last30days module
import last30days


def run_research(topic: str, save_dir: Path, lookback_days: int = 30) -> tuple[bool, dict]:
    """Run last30days research programmatically."""
    # Create a temporary directory for output
    with tempfile.TemporaryDirectory() as tmpdir:
        output_file = Path(tmpdir) / "report.json"

        # Build command line arguments
        sys_argv = [
            "last30days",
            "research",
            topic,
            "--save-dir", str(save_dir),
            "--format", "json",
            "--max-results", "20",
            "--recency-days", "30",
        ]

        # Save original sys.argv
        old_argv = sys.argv
        sys.argv = sys_argv

        try:
            # Call the main function
            exit_code = last30days.main()

            # Look for output file
            output_files = list(save_dir.glob("*.json"))
            if output_files:
                latest = max(output_files, key=lambda f: f.stat().st_mtime)
                with open(latest) as f:
                    data = json.load(f)
                return True, data
            else:
                return False, {"error": "No output file generated"}
        except SystemExit as e:
            if e.code != 0:
                return False, {"error": f"last30days exited with code {e.code}"}
            return False, {"error": "last30days exited with code 0 but no output"}
        except Exception as e:
            return False, {"error": f"last30days error: {e}"}
        finally:
            sys.argv = sys.argv[:1]  # Restore


def fallback_research(topic: str) -> dict:
    """Simple fallback research."""
    return {
        "topic": topic,
        "summary": f"Research summary for: {topic}",
        "sources": [],
        "findings": [],
        "note": "Generated via fallback; last30days skill not available",
    }


def run_research_sync(topic: str, save_dir: Path, lookback_days: int = 30) -> tuple[bool, dict]:
    """Synchronous wrapper for run_research."""
    return run_research(topic, save_dir, lookback_days)


if __name__ == "__main__":
    # Test
    import sys
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
        success, data = run_research(topic, Path("./test_output"))
        print(json.dumps(data, indent=2))