import copy
import importlib.util
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "inspection" / "plan.py"
SPEC = importlib.util.spec_from_file_location("inspection_plan", SCRIPT)
inspection = importlib.util.module_from_spec(SPEC)
sys.modules["inspection_plan"] = inspection
SPEC.loader.exec_module(inspection)

MECHANISMS = [
    "github-workflow",
    "cline-schedule",
    "pc-cron",
    "pc-systemd-timer",
    "pc-task-scheduler",
    "pc-launchd",
    "pc-startup",
    "harness",
]


def unit(
    identifier,
    mechanism="github-workflow",
    cadence="5m",
    severity="P1",
    status="active",
    target=None,
    evidence=None,
    trigger=None,
):
    return {
        "id": identifier,
        "mechanism": mechanism,
        "title": identifier,
        "target": target if target is not None else {"repo": "test/repo", "workflow": identifier},
        "trigger": trigger
        or ("schedule" if mechanism == "github-workflow" else "timer"),
        "cadence": cadence,
        "severity": severity,
        "owner": "巡检任务执行方案处",
        "status": status,
        "evidence": evidence if evidence is not None else ["gh run list --limit 1"],
    }


def gap(mechanism):
    return {
        "mechanism": mechanism,
        "status": "pending-inventory",
        "reason": f"{mechanism} not inventoried",
        "action": f"inventory every {mechanism} unit",
    }


def registry_with_units(units, gaps=None, mechanisms=None):
    mechanisms = list(mechanisms or MECHANISMS)
    gaps = gaps if gaps is not None else []
    return {
        "schema": inspection.SCHEMA,
        "updated_at": "2026-10-23T00:00:00Z",
        "directive": "DIRECTIVE.md",
        "principle": inspection.PRINCIPLE,
        "cadence_policy": {"floor": "5m", "ceiling": "7d", "default": "1h"},
        "supported_mechanisms": mechanisms,
        "units": units,
        "gaps": gaps,
    }


def complete_registry(units):
    """Cover every mechanism with a gap except the ones that have units."""
    with_units = {item["mechanism"] for item in units}
    gaps = [gap(mechanism) for mechanism in MECHANISMS if mechanism not in with_units]
    return registry_with_units(units, gaps)


def write_registry(directory, payload):
    path = Path(directory) / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def run_main(arguments, runner=None):
    captured = io.StringIO()
    with redirect_stdout(captured):
        if runner is None:
            code = inspection.main(arguments)
        else:
            code = inspection.main(arguments, runner=runner)
    return code, captured.getvalue()


# ---------------------------------------------------------------------------
# The shipped registry is the first regression: it must stay valid
# ---------------------------------------------------------------------------


def test_shipped_registry_passes_the_minimum_granularity_check():
    registry = inspection.load_registry()
    assert inspection.validate(registry) == []
    report = inspection.coverage(registry)
    assert report["units"] == 5
    assert report["active_units"] == 5
    assert report["mechanism_coverage"] == 1.0
    assert report["inventory_complete"] is False
    assert "cline-schedule" in report["pending_inventory"]


def test_duplicate_unit_id_is_rejected():
    first = unit("gh:test/monitor")
    registry = complete_registry([first, copy.deepcopy(first)])
    problems = inspection.validate(registry)
    assert any("duplicate unit id" in problem for problem in problems)


def test_aggregated_target_is_rejected():
    registry = complete_registry(
        [unit("gh:test/monitor", target={"workflows": ["a", "b"]})]
    )
    problems = inspection.validate(registry)
    assert any("must be scalar" in problem for problem in problems)

    registry = complete_registry(
        [unit("gh:test/monitor", target={"workflow": "whitehouse, trade"})]
    )
    problems = inspection.validate(registry)
    assert any("looks aggregated" in problem for problem in problems)

    registry = complete_registry(
        [unit("gh:test/monitor", target={"workflow": "*"})]
    )
    problems = inspection.validate(registry)
    assert any("looks aggregated" in problem for problem in problems)


def test_unit_without_evidence_is_rejected():
    registry = complete_registry([unit("gh:test/monitor", evidence=[])])
    problems = inspection.validate(registry)
    assert any("evidence" in problem for problem in problems)


def test_cadence_outside_the_policy_is_rejected():
    registry = complete_registry([unit("gh:test/monitor", cadence="1m")])
    assert any("outside policy" in problem for problem in inspection.validate(registry))

    registry = complete_registry([unit("gh:test/monitor", cadence="9d")])
    assert any("outside policy" in problem for problem in inspection.validate(registry))

    registry = complete_registry([unit("gh:test/monitor", cadence="hourly")])
    assert any("invalid cadence" in problem for problem in inspection.validate(registry))


def test_unsupported_and_uncovered_mechanisms_are_rejected():
    registry = complete_registry([unit("gh:test/monitor")])
    registry["supported_mechanisms"].append("zombie-cron")
    problems = inspection.validate(registry)
    assert any("'zombie-cron' has no unit and no declared gap" in p for p in problems)

    registry = complete_registry([unit("gh:test/monitor")])
    registry["gaps"].append(gap("zombie-cron"))
    problems = inspection.validate(registry)
    assert any("gap: unsupported mechanism" in problem for problem in problems)


def test_gap_for_a_registered_mechanism_is_a_contradiction():
    registry = complete_registry([unit("gh:test/monitor")])
    registry["gaps"].append(gap("github-workflow"))
    problems = inspection.validate(registry)
    assert any("has registered units and an open gap" in problem for problem in problems)


# ---------------------------------------------------------------------------
# Grid, coverage and GitHub reconciliation
# ---------------------------------------------------------------------------


def test_plan_uses_each_units_own_cadence():
    registry = complete_registry(
        [
            unit("gh:test/monitor", cadence="5m", severity="P1"),
            unit("gh:test/deploy", cadence="1h", severity="P2"),
            unit("gh:test/archive", cadence="1d", severity="P3"),
        ]
    )
    at = datetime(2026, 10, 23, 2, 20, tzinfo=timezone.utc)

    hourly = inspection.plan(registry, at=at, horizon="60m")
    assert [item["unit_id"] for item in hourly] == [
        "gh:test/monitor",
        "gh:test/deploy",
    ]
    assert hourly[0]["due_at"] == "2026-10-23T02:20:00Z"
    assert hourly[1]["due_at"] == "2026-10-23T03:00:00Z"

    short = inspection.plan(registry, at=at, horizon="5m")
    assert [item["unit_id"] for item in short] == ["gh:test/monitor"]

    daily = inspection.plan(registry, at=at, horizon="1d")
    assert "gh:test/archive" in [item["unit_id"] for item in daily]


def test_plan_skips_units_that_are_not_active():
    registry = complete_registry(
        [
            unit("gh:test/monitor", status="paused"),
            unit("gh:test/deploy", cadence="1h"),
        ]
    )
    at = datetime(2026, 10, 23, 2, 20, tzinfo=timezone.utc)
    due = inspection.plan(registry, at=at, horizon="60m")
    assert [item["unit_id"] for item in due] == ["gh:test/deploy"]


def test_coverage_reports_inventory_completeness():
    units = [unit(f"gh:test/{name}") for name in ("a", "b")]
    registry = complete_registry(units)
    report = inspection.coverage(registry)
    assert report["mechanism_coverage"] == 1.0
    assert report["inventory_complete"] is False
    assert "pc-cron" in report["pending_inventory"]

    fully_inventoried = registry_with_units(
        units
        + [
            unit("cline:acct/task", mechanism="cline-schedule", trigger="timer"),
            unit("pc:cron/host/line", mechanism="pc-cron", trigger="timer"),
            unit("pc:systemd/host/unit", mechanism="pc-systemd-timer", trigger="timer"),
            unit("pc:schtasks/host/task", mechanism="pc-task-scheduler", trigger="timer"),
            unit("pc:launchd/host/label", mechanism="pc-launchd", trigger="login"),
            unit("pc:startup/host/item", mechanism="pc-startup", trigger="boot"),
            unit("harness:cline/instance/task", mechanism="harness", trigger="manual"),
        ]
    )
    report = inspection.coverage(fully_inventoried)
    assert report["inventory_complete"] is True
    assert report["mechanism_coverage"] == 1.0
    assert report["units"] == 9


def test_reconcile_flags_missing_and_unregistered_workflows():
    registry = complete_registry(
        [
            unit(
                "gh:test/monitor",
                target={"repo": "test/repo", "workflow": "Monitor", "workflow_id": "1"},
            )
        ]
    )
    live = [
        {"id": 1, "name": "Monitor", "path": ".github/workflows/monitor.yml"},
        {"id": 2, "name": "Extra", "path": ".github/workflows/extra.yml"},
    ]
    problems = inspection.reconcile_github(registry, workflows=live)
    assert any("unregistered GitHub workflow: Extra (2)" in p for p in problems)
    assert not any("missing on GitHub" in p for p in problems)

    problems = inspection.reconcile_github(registry, workflows=[])
    assert any("registered workflow missing on GitHub" in p for p in problems)


def test_reconcile_ignores_units_from_another_repository():
    registry = complete_registry(
        [
            unit(
                "gh:other/monitor",
                target={"repo": "other/repo", "workflow": "Monitor", "workflow_id": "7"},
            )
        ]
    )
    live = [{"id": 1, "name": "Monitor", "path": ".github/workflows/monitor.yml"}]
    problems = inspection.reconcile_github(registry, workflows=live, repo="test/repo")
    assert any("unregistered GitHub workflow" in p for p in problems)
    assert not any("missing on GitHub" in p for p in problems)


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------


def test_cli_check_json_reports_a_broken_registry():
    with tempfile.TemporaryDirectory() as tmp:
        broken = complete_registry([unit("gh:test/a"), unit("gh:test/a")])
        path = write_registry(tmp, broken)
        code, output = run_main(["check", "--registry", str(path), "--json"])
        assert code == 1
        payload = json.loads(output)
        assert payload["ok"] is False
        assert any("duplicate unit id" in problem for problem in payload["problems"])


def test_cli_check_degrades_when_gh_is_unavailable():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_registry(tmp, inspection.load_registry())

        def broken_runner(arguments):
            raise inspection.GhUnavailable("gh is not installed")

        code, output = run_main(
            ["check", "--registry", str(path), "--github", "--json"],
            runner=broken_runner,
        )
        assert code == 0
        payload = json.loads(output)
        assert "gh is not installed" in payload["warning"]

        code, _ = run_main(
            ["check", "--registry", str(path), "--github", "--strict", "--json"],
            runner=broken_runner,
        )
        assert code == 1


def test_cli_plan_renders_the_shipped_grid():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_registry(tmp, inspection.load_registry())
        code, output = run_main(
            [
                "plan",
                "--registry",
                str(path),
                "--at",
                "2026-10-23T02:20:00Z",
                "--horizon",
                "60m",
                "--json",
            ]
        )
        assert code == 0
        payload = json.loads(output)
        assert [item["unit_id"] for item in payload["due"]] == [
            "gh:gitsynonyms/monitor-whitehouse",
            "gh:gitsynonyms/deploy-subtitle-page",
        ]


def test_cli_check_text_names_the_pending_inventory():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_registry(tmp, inspection.load_registry())
        code, output = run_main(["check", "--registry", str(path)])
        assert code == 0
        assert "inventory incomplete" in output
        assert "cline-schedule" in output


def test_cli_missing_registry_exits_two():
    code, _ = run_main(
        ["coverage", "--registry", "/nonexistent/registry.json", "--json"]
    )
    assert code == 2


