import copy
import importlib.util
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "resources" / "check.py"
SPEC = importlib.util.spec_from_file_location("resources_check", SCRIPT)
resources = importlib.util.module_from_spec(SPEC)
sys.modules["resources_check"] = resources
SPEC.loader.exec_module(resources)

SOURCES = {
    "whitehouse-streams": "https://www.youtube.com/@WhiteHouse/streams",
    "whitehouse-youtube": "https://www.youtube.com/@WhiteHouse/videos",
    "whitehouse-news": "https://www.whitehouse.gov/news/feed/",
}


def resource(
    identifier="yt:@WhiteHouse/streams",
    *,
    url="https://www.youtube.com/@WhiteHouse/streams",
    source="whitehouse-streams",
    designated=True,
    status="active",
    **overrides,
):
    entry = {
        "id": identifier,
        "kind": "youtube-channel",
        "url": url,
        "designated": designated,
        "trust": "official",
        "cadence": "5m",
        "analysis": ["event-detection"],
        "collector": {"script": "scripts/monitoring/acquisition.py", "source": source},
        "status": status,
    }
    entry.update(overrides)
    return entry


def make_registry(entries):
    return {
        "schema": resources.SCHEMA,
        "updated_at": "2026-10-23T00:00:00Z",
        "owner": resources.OWNER,
        "designated_by": resources.DESIGNATED_BY,
        "principle": resources.PRINCIPLE,
        "cadence_policy": {"floor": "5m", "ceiling": "7d"},
        "resources": entries,
    }


def required_pair():
    return [
        resource("yt:@WhiteHouse/streams"),
        resource(
            "yt:@WhiteHouse/videos",
            url="https://www.youtube.com/@WhiteHouse/videos",
            source="whitehouse-youtube",
        ),
    ]


def validate(registry):
    return resources.validate(registry, sources=SOURCES)


def run_main(arguments):
    captured = io.StringIO()
    with redirect_stdout(captured):
        code = resources.main(arguments)
    return code, captured.getvalue()


def write_registry(directory, payload):
    path = Path(directory) / "registry.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Shipped registry and designated coverage
# ---------------------------------------------------------------------------


def test_shipped_registry_passes_and_covers_designated_resources():
    registry = resources.load_registry()
    assert resources.validate(registry) == []
    report = resources.coverage(registry)
    assert report["resources"] == 6
    assert report["designated"] == 2
    assert report["trackable_designated"] == 2
    assert report["designated_coverage"] == 1.0
    assert report["designated_ids"] == [
        "yt:@WhiteHouse/streams",
        "yt:@WhiteHouse/videos",
    ]


def test_missing_designated_resource_is_rejected():
    registry = make_registry([resource()])
    problems = validate(registry)
    assert any(
        "designated resource is missing: https://www.youtube.com/@WhiteHouse/videos" in p
        for p in problems
    )


def test_designated_flag_and_retirement_are_enforced():
    entries = required_pair()
    entries[0]["designated"] = False
    assert any("lost its flag" in p for p in validate(make_registry(entries)))

    entries = required_pair()
    entries[0]["status"] = "retired"
    entries[0]["successor"] = "yt:@WhiteHouse/videos"
    assert any("cannot be retired" in p for p in validate(make_registry(entries)))


def test_duplicate_resource_id_is_rejected():
    entries = required_pair()
    entries.append(copy.deepcopy(entries[0]))
    problems = validate(make_registry(entries))
    assert any("duplicate resource id" in p for p in problems)


# ---------------------------------------------------------------------------
# One URL per record and field checks
# ---------------------------------------------------------------------------


def test_multiple_urls_in_one_record_are_rejected():
    entries = required_pair()
    entries[0]["url"] = (
        "https://www.youtube.com/@WhiteHouse/streams "
        "https://www.youtube.com/@WhiteHouse/videos"
    )
    problems = validate(make_registry(entries))
    assert any("exactly one address" in p for p in problems)
    assert any("separators or whitespace" in p for p in problems)

    entries = required_pair()
    entries[0]["url"] = (
        "https://www.youtube.com/@WhiteHouse/streams,"
        "https://www.youtube.com/@WhiteHouse/videos"
    )
    assert any("separators" in p for p in validate(make_registry(entries)))


def test_invalid_fields_are_rejected():
    entries = required_pair()
    entries[0].update(
        {
            "kind": "magazine",
            "trust": "vibes",
            "cadence": "1m",
            "status": "sleeping",
            "analysis": [],
        }
    )
    problems = validate(make_registry(entries))
    assert any("kind must be" in p for p in problems)
    assert any("trust must be" in p for p in problems)
    assert any("outside policy" in p for p in problems)
    assert any("status must be" in p for p in problems)
    assert any("analysis must list" in p for p in problems)


def test_collector_must_exist_and_match_the_url():
    entries = required_pair()
    entries[0]["collector"] = {"script": "scripts/missing.py", "source": "whitehouse-streams"}
    assert any("collector.script does not exist" in p for p in validate(make_registry(entries)))

    entries = required_pair()
    entries[0]["collector"] = {
        "script": "scripts/monitoring/acquisition.py",
        "source": "nope",
    }
    assert any("is not in the acquisition registry" in p for p in validate(make_registry(entries)))

    entries = required_pair()
    entries[0]["url"] = "https://www.youtube.com/@WhiteHouse/videos"
    entries[0]["collector"] = {
        "script": "scripts/monitoring/acquisition.py",
        "source": "whitehouse-streams",
    }
    problems = validate(make_registry(entries))
    assert any("which is not the registered url" in p for p in problems)


def test_retired_resource_needs_a_registered_successor():
    entries = required_pair()
    entries.append(
        resource(
            "rss:old-feed",
            url="https://www.whitehouse.gov/news/feed/",
            source="whitehouse-news",
            designated=False,
            status="retired",
        )
    )
    problems = validate(make_registry(entries))
    assert any("must name a successor" in p for p in problems)

    entries[-1]["successor"] = "rss:missing"
    assert any("is not registered" in p for p in validate(make_registry(entries)))

    entries[-1]["successor"] = "yt:@WhiteHouse/videos"
    assert validate(make_registry(entries)) == []


def test_coverage_drops_for_degraded_designated_resources():
    entries = required_pair()
    entries[0]["status"] = "degraded"
    report = resources.coverage(make_registry(entries), sources=SOURCES)
    assert report["designated"] == 2
    assert report["trackable_designated"] == 1
    assert report["designated_coverage"] == 0.5
    assert report["by_status"] == {"active": 1, "degraded": 1}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_check_json_and_list():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_registry(tmp, resources.load_registry())
        code, output = run_main(["check", "--registry", str(path), "--json"])
        assert code == 0
        payload = json.loads(output)
        assert payload["ok"] is True
        assert payload["coverage"]["designated_coverage"] == 1.0

        code, output = run_main(["list", "--registry", str(path), "--json"])
        assert code == 0
        listed = json.loads(output)["resources"]
        assert len(listed) == 6
        assert sum(1 for item in listed if item["designated"]) == 2


def test_cli_reports_violations_with_exit_one():
    with tempfile.TemporaryDirectory() as tmp:
        registry = make_registry(required_pair())
        registry["resources"][0]["url"] = "not-a-url"
        path = write_registry(tmp, registry)
        code, output = run_main(["check", "--registry", str(path), "--json"])
        assert code == 1
        payload = json.loads(output)
        assert payload["ok"] is False
        assert any("absolute http(s)" in p for p in payload["problems"])


def test_cli_missing_registry_exits_two():
    code, _ = run_main(["check", "--registry", "/nonexistent/resources.json", "--json"])
    assert code == 2

