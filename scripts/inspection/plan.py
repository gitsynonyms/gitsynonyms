#!/usr/bin/env python3
"""巡检任务执行方案处 (inspection task execution planning office).

Enforces the minimum granularity rule of ``DIRECTIVE.md`` and turns the
registry into an executable inspection grid:

    validate   one record per smallest independently observable unit
    coverage   how much of the declared mechanism surface is registered
    plan       which units are due in the next horizon, per unit cadence
    reconcile  GitHub workflows registered here versus visible through gh

Only the Python standard library is required. ``gh`` is used by ``--github``
and its absence degrades to a warning unless ``--strict`` is given.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "inspection" / "registry.json"
SCHEMA = "gitsynonyms.inspection.registry/1"
PRINCIPLE = "minimum-granularity"

UTC = timezone.utc
TRIGGERS = {
    "schedule",
    "workflow_dispatch",
    "push",
    "pull_request",
    "manual",
    "timer",
    "login",
    "boot",
    "unknown",
}
SEVERITIES = ("P0", "P1", "P2", "P3")
STATUSES = {"active", "paused", "retired", "pending-inventory"}
UNIT_ID = re.compile(r"^[a-z0-9][a-z0-9:/._-]*$")
CADENCE = re.compile(r"^(\d+)\s*([mhdw])$")
CADENCE_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080}
AGGREGATE_WORDS = re.compile(r"(?<![a-z])(all|any)(?![a-z])")
AGGREGATE_MARKS = ("*", "全部", "、")
MAX_TARGET_VALUE = 300


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def parse_cadence(value: str) -> int:
    """Return a cadence in minutes; ``5m``, ``6h``, ``1d``, ``1w``."""
    match = CADENCE.match((value or "").strip().lower())
    if not match:
        raise ValueError(f"invalid cadence: {value!r}")
    minutes = int(match.group(1)) * CADENCE_MINUTES[match.group(2)]
    if minutes <= 0:
        raise ValueError(f"cadence must be positive: {value!r}")
    return minutes


def load_registry(path: Path | str = DEFAULT_REGISTRY) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Minimum-granularity validation
# ---------------------------------------------------------------------------


def _target_problems(target: object, unit_id: str) -> list[str]:
    problems = []
    if not isinstance(target, dict) or not target:
        return [f"{unit_id}: target must be a non-empty object"]
    for key, value in target.items():
        if isinstance(value, (list, tuple, dict, set)):
            problems.append(
                f"{unit_id}: target[{key}] must be scalar; arrays merge units into one record"
            )
            continue
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            problems.append(f"{unit_id}: target[{key}] has unsupported type")
            continue
        text = str(value).strip()
        if not text:
            problems.append(f"{unit_id}: target[{key}] is empty")
            continue
        if len(text) > MAX_TARGET_VALUE:
            problems.append(f"{unit_id}: target[{key}] is too long to be one unit")
        lowered = text.lower()
        if (
            any(mark in text for mark in AGGREGATE_MARKS)
            or AGGREGATE_WORDS.search(lowered)
            or "," in text
            or ";" in text
        ):
            problems.append(
                f"{unit_id}: target[{key}] looks aggregated ({text!r}); "
                "one record per independently observable unit"
            )
    return problems


def _unit_problems(
    unit: object, mechanisms: set[str], floor: int, ceiling: int
) -> list[str]:
    if not isinstance(unit, dict):
        return ["unit entry must be an object"]
    identifier = str(unit.get("id") or "")
    label = identifier or "<missing id>"
    problems = []
    if not UNIT_ID.match(identifier):
        problems.append(f"{label}: id must match {UNIT_ID.pattern}")
    if unit.get("mechanism") not in mechanisms:
        problems.append(f"{label}: unsupported mechanism {unit.get('mechanism')!r}")
    if not str(unit.get("title") or "").strip():
        problems.append(f"{label}: title is required")
    problems.extend(_target_problems(unit.get("target"), label))
    if unit.get("trigger") not in TRIGGERS:
        problems.append(f"{label}: trigger must be one of {sorted(TRIGGERS)}")
    if unit.get("severity") not in SEVERITIES:
        problems.append(f"{label}: severity must be one of {list(SEVERITIES)}")
    if unit.get("status") not in STATUSES:
        problems.append(f"{label}: status must be one of {sorted(STATUSES)}")
    if not str(unit.get("owner") or "").strip():
        problems.append(f"{label}: owner is required")
    cadence = unit.get("cadence")
    try:
        minutes = parse_cadence(str(cadence or ""))
        if not floor <= minutes <= ceiling:
            problems.append(
                f"{label}: cadence {cadence!r} outside policy {floor}m..{ceiling}m"
            )
    except ValueError as error:
        problems.append(f"{label}: {error}")
    evidence = unit.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        problems.append(f"{label}: evidence must list at least one reproducible command")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, str) or not item.strip():
                problems.append(f"{label}: evidence[{index}] must be a non-empty string")
    return problems


def validate(registry: object) -> list[str]:
    """Every violation of DIRECTIVE.md's minimum-granularity rule, in order."""
    if not isinstance(registry, dict):
        return ["registry root must be an object"]
    problems: list[str] = []
    if registry.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r}")
    if registry.get("principle") != PRINCIPLE:
        problems.append(f"principle must be {PRINCIPLE!r}")
    policy = registry.get("cadence_policy")
    floor, ceiling = 5, 7 * 1440
    if not isinstance(policy, dict):
        problems.append("cadence_policy must be an object")
    else:
        try:
            floor = parse_cadence(str(policy.get("floor")))
            ceiling = parse_cadence(str(policy.get("ceiling")))
            if floor > ceiling:
                problems.append("cadence_policy floor must not exceed ceiling")
        except ValueError as error:
            problems.append(f"cadence_policy: {error}")

    declared = registry.get("supported_mechanisms")
    mechanisms: set[str] = set()
    if not isinstance(declared, list) or not declared:
        problems.append("supported_mechanisms must be a non-empty list")
    else:
        for mechanism in declared:
            if not isinstance(mechanism, str) or not mechanism.strip():
                problems.append("supported_mechanisms entries must be non-empty strings")
                continue
            if mechanism in mechanisms:
                problems.append(f"supported_mechanisms repeats {mechanism!r}")
            mechanisms.add(mechanism)

    units = registry.get("units")
    if not isinstance(units, list):
        problems.append("units must be a list")
        units = []
    seen: set[str] = set()
    unit_mechanisms: set[str] = set()
    for unit in units:
        problems.extend(_unit_problems(unit, mechanisms, floor, ceiling))
        if not isinstance(unit, dict):
            continue
        identifier = unit.get("id")
        if isinstance(identifier, str) and identifier:
            if identifier in seen:
                problems.append(f"{identifier}: duplicate unit id")
            seen.add(identifier)
        mechanism = unit.get("mechanism")
        if isinstance(mechanism, str):
            unit_mechanisms.add(mechanism)

    gaps = registry.get("gaps") or []
    if not isinstance(gaps, list):
        problems.append("gaps must be a list")
        gaps = []
    gap_mechanisms: set[str] = set()
    for gap in gaps:
        if not isinstance(gap, dict):
            problems.append("gap entry must be an object")
            continue
        mechanism = gap.get("mechanism")
        if mechanism not in mechanisms:
            problems.append(f"gap: unsupported mechanism {mechanism!r}")
            continue
        if mechanism in gap_mechanisms:
            problems.append(f"gap repeats mechanism {mechanism!r}")
        gap_mechanisms.add(mechanism)
        if gap.get("status") not in {"pending-inventory", "accepted"}:
            problems.append(
                f"gap[{mechanism}]: status must be pending-inventory or accepted"
            )
        for field in ("reason", "action"):
            if not str(gap.get(field) or "").strip():
                problems.append(f"gap[{mechanism}]: {field} is required")

    for mechanism in sorted(mechanisms):
        has_unit = mechanism in unit_mechanisms
        has_gap = mechanism in gap_mechanisms
        if has_unit and has_gap:
            problems.append(
                f"mechanism {mechanism!r} has registered units and an open gap"
            )
        elif not has_unit and not has_gap:
            problems.append(
                f"mechanism {mechanism!r} has no unit and no declared gap"
            )
    return problems


# ---------------------------------------------------------------------------
# Coverage and the per-unit inspection grid
# ---------------------------------------------------------------------------


def coverage(registry: dict) -> dict:
    mechanisms = [str(item) for item in registry.get("supported_mechanisms") or []]
    units = [unit for unit in registry.get("units") or [] if isinstance(unit, dict)]
    gaps = [gap for gap in registry.get("gaps") or [] if isinstance(gap, dict)]
    by_mechanism: dict[str, dict] = {
        mechanism: {"units": 0, "active": 0, "gap": ""} for mechanism in mechanisms
    }
    for unit in units:
        mechanism = str(unit.get("mechanism") or "")
        entry = by_mechanism.setdefault(
            mechanism, {"units": 0, "active": 0, "gap": ""}
        )
        entry["units"] += 1
        if unit.get("status") == "active":
            entry["active"] += 1
    for gap in gaps:
        mechanism = str(gap.get("mechanism") or "")
        entry = by_mechanism.setdefault(
            mechanism, {"units": 0, "active": 0, "gap": ""}
        )
        entry["gap"] = str(gap.get("status") or "")
    covered = [
        mechanism
        for mechanism in mechanisms
        if by_mechanism[mechanism]["units"] or by_mechanism[mechanism]["gap"]
    ]
    return {
        "mechanisms": len(mechanisms),
        "mechanism_coverage": (
            round(len(covered) / len(mechanisms), 3) if mechanisms else 0.0
        ),
        "units": len(units),
        "active_units": sum(1 for unit in units if unit.get("status") == "active"),
        "pending_inventory": sorted(
            mechanism
            for mechanism, entry in by_mechanism.items()
            if entry["gap"] == "pending-inventory"
        ),
        "inventory_complete": not any(
            entry["gap"] == "pending-inventory" for entry in by_mechanism.values()
        ),
        "by_mechanism": by_mechanism,
    }


def next_due(unit: dict, at: datetime) -> datetime:
    """The next boundary of this unit's own cadence, on a UTC grid."""
    step = parse_cadence(str(unit.get("cadence") or "")) * 60
    epoch = int(at.timestamp())
    boundary = ((epoch + step - 1) // step) * step
    return datetime.fromtimestamp(boundary, tz=UTC)


def plan(registry: dict, *, at: datetime | None = None, horizon: str = "1h") -> list[dict]:
    """Active units whose next boundary falls inside the horizon."""
    moment = at or utc_now()
    until = moment + timedelta(minutes=parse_cadence(horizon))
    due: list[dict] = []
    for unit in registry.get("units") or []:
        if not isinstance(unit, dict) or unit.get("status") != "active":
            continue
        try:
            boundary = next_due(unit, moment)
        except ValueError:
            continue
        if boundary > until:
            continue
        due.append(
            {
                "unit_id": unit.get("id"),
                "mechanism": unit.get("mechanism"),
                "title": unit.get("title"),
                "severity": unit.get("severity"),
                "cadence": unit.get("cadence"),
                "due_at": iso(boundary),
                "evidence": list(unit.get("evidence") or []),
            }
        )
    due.sort(key=lambda item: (item["due_at"], str(item["unit_id"])))
    return due


# ---------------------------------------------------------------------------
# GitHub reconciliation (one workflow = one unit)
# ---------------------------------------------------------------------------


class GhUnavailable(RuntimeError):
    """gh is missing or cannot answer; the caller decides whether that fails."""


def run_gh(args: Sequence[str]) -> str:
    binary = shutil.which("gh")
    if not binary:
        raise GhUnavailable("gh is not installed")
    try:
        completed = subprocess.run(
            [binary, *args], capture_output=True, text=True, timeout=45
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GhUnavailable(str(error)) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"gh exited {completed.returncode}"
        raise GhUnavailable(detail)
    return completed.stdout


def github_workflows(runner: Callable[[Sequence[str]], str] = run_gh) -> list[dict]:
    payload = runner(["workflow", "list", "--all", "--json", "name,id,state,path"])
    data = json.loads(payload)
    if not isinstance(data, list):
        raise GhUnavailable("gh returned an unexpected payload")
    return [item for item in data if isinstance(item, dict)]


def _registry_repo(registry: dict) -> str:
    for unit in registry.get("units") or []:
        if not isinstance(unit, dict) or unit.get("mechanism") != "github-workflow":
            continue
        repo = (unit.get("target") or {}).get("repo")
        if repo:
            return str(repo)
    return ""


def reconcile_github(
    registry: dict,
    workflows: Sequence[dict] | None = None,
    runner: Callable[[Sequence[str]], str] = run_gh,
    repo: str = "",
) -> list[str]:
    """Registered workflows that vanished, and live workflows nobody registered."""
    items = list(workflows) if workflows is not None else github_workflows(runner)
    slug = repo or _registry_repo(registry)
    by_id = {str(item.get("id")): item for item in items if isinstance(item, dict)}
    by_name = {
        str(item.get("name")): item
        for item in items
        if isinstance(item, dict) and item.get("name")
    }
    registered: set[str] = set()
    problems: list[str] = []
    for unit in registry.get("units") or []:
        if not isinstance(unit, dict) or unit.get("mechanism") != "github-workflow":
            continue
        target = unit.get("target") or {}
        if slug and target.get("repo") and str(target.get("repo")) != slug:
            continue
        workflow_id = str(target.get("workflow_id") or "")
        name = str(target.get("workflow") or "")
        found = by_id.get(workflow_id) or by_name.get(name)
        if found is None:
            problems.append(
                f"{unit.get('id')}: registered workflow missing on GitHub "
                f"({name or workflow_id})"
            )
        else:
            registered.add(str(found.get("id")))
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("id")) in registered:
            continue
        problems.append(
            f"unregistered GitHub workflow: {item.get('name')} ({item.get('id')})"
        )
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inspection-plan",
        description=(
            "巡检任务执行方案处 - minimum-granularity registry check, "
            "coverage and per-unit inspection grid"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("check", "validate the registry against the directive"),
        ("coverage", "report mechanism coverage and open gaps"),
        ("plan", "list active units due inside a horizon"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument(
            "--registry", default=str(DEFAULT_REGISTRY), help="registry file"
        )
        command.add_argument("--json", action="store_true", dest="as_json")
        if name == "check":
            command.add_argument(
                "--github",
                action="store_true",
                help="reconcile registered workflows with gh workflow list",
            )
            command.add_argument(
                "--strict",
                action="store_true",
                help="treat an unavailable gh as a violation",
            )
        if name == "plan":
            command.add_argument("--at", default="", help="ISO timestamp, default now")
            command.add_argument("--horizon", default="1h", help="e.g. 30m, 6h, 1d")
    return parser


def _print_report(command: str, report: dict, args) -> None:
    coverage_info = report["coverage"]
    state = "OK" if report["ok"] else "FAILED"
    print(
        f"registry {state}: {coverage_info['units']} units, "
        f"{coverage_info['mechanisms']} mechanisms, "
        f"mechanism coverage {coverage_info['mechanism_coverage']}"
    )
    if not coverage_info["inventory_complete"]:
        print(
            "inventory incomplete: "
            + ", ".join(coverage_info["pending_inventory"])
            + " still pending"
        )
    if command in {"coverage", "check"}:
        for mechanism, entry in coverage_info["by_mechanism"].items():
            detail = f"{entry['units']} unit(s)"
            if entry["gap"]:
                detail += f", gap={entry['gap']}"
            print(f"  {mechanism:<20} {detail}")
    if command == "plan":
        due = report.get("due") or []
        print(f"due at {report.get('at')} within {report.get('horizon')}: {len(due)}")
        for item in due:
            print(
                f"  {item['due_at']}  {item['severity']:<2} {item['unit_id']} "
                f"({item['cadence']})"
            )
    if report.get("warning"):
        print(f"warning: {report['warning']}")
    for problem in report["problems"]:
        print(f"  violation: {problem}")


def main(argv: Sequence[str] | None = None, *, runner=run_gh) -> int:
    args = build_parser().parse_args(argv)
    try:
        registry = load_registry(args.registry)
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: cannot read registry: {error}", file=sys.stderr)
        return 2
    problems = validate(registry)
    warning = ""
    if args.command == "check" and args.github:
        try:
            problems.extend(reconcile_github(registry, runner=runner))
        except GhUnavailable as error:
            warning = f"github reconciliation skipped: {error}"
            if args.strict:
                problems.append(warning)
    report = {
        "ok": not problems,
        "problems": problems,
        "coverage": coverage(registry),
    }
    if warning:
        report["warning"] = warning
    if args.command == "plan":
        moment = parse_moment(args.at) if args.at else utc_now()
        report["at"] = iso(moment)
        report["horizon"] = args.horizon
        report["due"] = (
            plan(registry, at=moment, horizon=args.horizon) if not problems else []
        )
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(args.command, report, args)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())





