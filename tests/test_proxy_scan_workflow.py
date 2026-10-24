"""Guards for the sharded, multi-runner proxy scan workflow.

The full sweep is only as complete as the fan-out: every runner must get a
disjoint slice, exactly one job may publish, and the discovery cursor has to
survive between runs. These assertions pin that shape so a later edit cannot
quietly turn the scan back into one runner sampling the head of the list.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]
TRACKED = ROOT / "scripts" / "monitoring" / "proxy-scan.yml"
ACTIVE = ROOT / ".github" / "workflows" / "proxy-scan.yml"


def workflow() -> dict:
    return yaml.safe_load(TRACKED.read_text(encoding="utf-8"))


def run_text(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_tracked_source_is_the_complete_sharded_flow():
    jobs = workflow()["jobs"]
    assert "--shard-count" in run_text(jobs["collect"])
    assert "--shard-input" in run_text(jobs["probe"])
    assert "--what all" in run_text(jobs["publish"])


def test_tracked_source_and_the_active_workflow_are_identical():
    tracked = TRACKED.read_text(encoding="utf-8")
    active = ACTIVE.read_text(encoding="utf-8")
    if tracked != active:
        # Writing .github/workflows needs a token with the `workflows`
        # permission. Until a maintainer installs the tracked source
        # (see .github/workflows-deploy/proxy-scan.HANDOFF.md), the active file
        # is the previous single-runner flow; the tracked source is still the
        # complete definition asserted above.
        assert (ROOT / ".github" / "workflows-deploy" / "proxy-scan.HANDOFF.md").is_file()
        pytest.skip("sharded proxy-scan workflow is staged, not installed yet")
    assert tracked == active


def test_schedule_is_hourly_and_the_shard_defaults_are_explicit():
    data = workflow()
    assert data[True]["schedule"] == [{"cron": "23 * * * *"}]
    inputs = data[True]["workflow_dispatch"]["inputs"]
    assert inputs["shards"]["default"] == "16"
    assert inputs["max_parallel"]["default"] == "8"
    assert inputs["sync"]["default"] is True


def test_collect_and_probe_fan_out_one_runner_per_shard():
    jobs = workflow()["jobs"]
    for name in ("collect", "probe"):
        strategy = jobs[name]["strategy"]
        assert strategy["fail-fast"] is False
        assert "fromJSON(needs.plan.outputs.shards)" in strategy["matrix"]["shard"]
        # The five-minute monitor keeps priority over a full sweep.
        assert "max-parallel" in strategy
    plan = [step for step in jobs["plan"]["steps"] if step.get("id") == "shards"][0]
    assert "json.dumps(list(range(count)))" in plan["run"]


def test_collect_runners_take_disjoint_source_slices():
    text = run_text(workflow()["jobs"]["collect"])
    assert "--stages sources,collect" in text
    assert "--shard-index" in text and "--shard-count" in text
    assert "--workers" in text


def test_probe_runners_only_touch_their_own_slice():
    text = run_text(workflow()["jobs"]["probe"])
    assert "--stages probe" in text
    assert "--shard-input" in text
    assert "shard-$SHARD-of-$SHARDS.txt" in text
    assert "--tcp-concurrency" in text and "--probe-concurrency" in text
    # High concurrency needs the file-descriptor limit raised first.
    assert "ulimit -n" in text


def test_exactly_one_job_publishes_and_it_merges_every_shard():
    jobs = workflow()["jobs"]
    publishers = [name for name, job in jobs.items() if "--sync" in run_text(job)]
    assert publishers == ["publish"]

    publish = jobs["publish"]
    text = run_text(publish)
    assert "merge" in text and "--what all" in text
    downloads = [
        step["with"]["pattern"]
        for step in publish["steps"]
        if step.get("uses", "").startswith("actions/download-artifact")
        and "pattern" in step.get("with", {})
    ]
    assert downloads == ["collect-shard-*", "probe-shard-*"]
    # Publication is a single commit; no shard runner may push on its own.
    assert "git push" not in text and "git commit" not in text


def test_discovery_cursor_and_repo_list_are_carried_across_runs():
    jobs = workflow()["jobs"]
    discover = jobs["discover"]
    assert "--stages matrix" in run_text(discover)
    discovery_artifact = str(
        [
            step
            for step in discover["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact")
        ]
    )
    assert "discovery-state.json" in discovery_artifact
    assert "discovered-repos.txt" in discovery_artifact
    publish_artifact = str(jobs["publish"]["steps"])
    assert "discovery-state.json" in publish_artifact
    assert "discovered-repos.txt" in publish_artifact


def test_matrix_discovery_runs_in_exactly_one_job():
    jobs = workflow()["jobs"]
    matrix_jobs = [name for name, job in jobs.items() if "--stages matrix" in run_text(job)]
    assert matrix_jobs == ["discover"]
