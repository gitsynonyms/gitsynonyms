import importlib.util
import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "proxyindex" / "discover_terms.py"
SPEC = importlib.util.spec_from_file_location("discover_terms", SCRIPT)
discover = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discover)


def test_build_queries_covers_synonyms_permutations_and_scopes():
    queries = discover.build_queries()

    assert "proxy" in queries
    assert "proxy list" in queries
    assert "socks5 proxy list" in queries
    assert "免费代理池" in queries
    assert any("site:reddit.com" in value for value in queries)
    assert len(queries) == len(set(queries))


def test_build_queries_respects_groups_and_limit():
    core = discover.build_queries(groups=["core"])
    assert "proxy" in core
    assert "socks5 proxy list" not in core

    scoped = discover.build_queries(groups=["scope"])
    assert scoped
    assert all("site:" in value or "forum" in value or "telegram" in value for value in scoped)

    assert len(discover.build_queries(limit=5)) == 5


def test_build_queries_rejects_unknown_group():
    try:
        discover.build_queries(groups=["nope"])
    except ValueError as error:
        assert "unknown query groups" in str(error)
    else:
        raise AssertionError("Expected an unknown group to be rejected")


def test_run_last30days_uses_configured_script_and_parses_json(tmp_path, monkeypatch):
    script = tmp_path / "last30days.py"
    script.write_text("# stub\n", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"sources": [{"url": "https://example.com/a"}]}),
            "",
        )

    monkeypatch.setenv("LAST30DAYS_SCRIPT", str(script))
    monkeypatch.setattr(discover.subprocess, "run", fake_run)

    ok, data = discover.run_last30days("free proxy list", tmp_path / "save")

    assert ok is True
    assert data["sources"][0]["url"] == "https://example.com/a"
    command = captured["command"]
    assert command[1] == str(script)
    assert "--emit=json" in command
    assert "free proxy list" in command


def test_run_last30days_reports_missing_cli(tmp_path, monkeypatch):
    monkeypatch.delenv("LAST30DAYS_SCRIPT", raising=False)

    def fail(command, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(discover.subprocess, "run", fail)

    ok, data = discover.run_last30days("proxy", tmp_path)

    assert ok is False
    assert "not installed" in data["error"]


def test_run_last30days_reports_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("LAST30DAYS_SCRIPT", str(tmp_path / "last30days.py"))
    (tmp_path / "last30days.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(
        discover.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "not json", ""),
    )

    ok, data = discover.run_last30days("proxy", tmp_path)

    assert ok is False
    assert "invalid JSON" in data["error"]


def test_extract_source_urls_dedupes_and_keeps_only_absolute_urls():
    payload = {
        "sources": [
            {"url": "https://a.example/x"},
            {"url": "https://a.example/x"},
            {"link": "ftp://bad.example/file"},
            {"permalink": "http://b.example/y"},
        ],
        "results": [{"url": "https://c.example/z"}, "https://d.example/w"],
        "clusters": [{"sources": [{"url": "https://e.example/v"}]}],
    }

    assert discover.extract_source_urls(payload) == [
        "https://a.example/x",
        "http://b.example/y",
        "https://c.example/z",
        "https://d.example/w",
        "https://e.example/v",
    ]


def test_research_terms_records_failures_without_fabricating_sources(tmp_path, monkeypatch):
    calls = []

    def fake_run_last30days(query, save_dir, **kwargs):
        calls.append(query)
        if query == "proxy":
            return True, {"sources": [{"url": "https://example.com/list"}]}
        return False, {"error": "quota exhausted"}

    monkeypatch.setattr(discover, "run_last30days", fake_run_last30days)

    records, urls = discover.research_terms(["proxy", "socks5"], save_dir=tmp_path)

    assert calls == ["proxy", "socks5"]
    assert urls == ["https://example.com/list"]
    assert records[0]["ok"] is True
    assert records[0]["source_count"] == 1
    assert records[1]["ok"] is False
    assert records[1]["urls"] == []
    assert "quota exhausted" in records[1]["error"]


def test_merge_into_sources_appends_only_new_urls(tmp_path):
    sources = tmp_path / "sources.txt"
    sources.write_text("https://raw.example/known.txt\n", encoding="utf-8")

    added = discover.merge_into_sources(
        ["https://raw.example/known.txt", "https://raw.example/new.txt"], sources
    )

    assert added == 1
    assert sources.read_text(encoding="utf-8").splitlines() == [
        "https://raw.example/known.txt",
        "https://raw.example/new.txt",
    ]


def test_write_research_and_extra_round_trip(tmp_path):
    records = [{"query": "proxy", "ok": True, "urls": ["https://example.com"]}]
    log = tmp_path / "term_research.jsonl"
    extra = tmp_path / "sources_extra.txt"

    discover.write_research(records, log)
    discover.write_extra(records[0]["urls"], extra)

    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0])["query"] == "proxy"
    assert extra.read_text(encoding="utf-8") == "https://example.com\n"
