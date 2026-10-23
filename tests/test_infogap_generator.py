import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).parents[1] / "scripts" / "infogap" / "generate.py"
SPEC = importlib.util.spec_from_file_location("infogap_generator", SCRIPT)
generator = importlib.util.module_from_spec(SPEC)
sys.modules["infogap_generator"] = generator
SPEC.loader.exec_module(generator)


def test_report_html_escapes_external_content_and_rejects_unsafe_urls():
    report = generator.Report(
        generated_at="2026-10-23T00:00:00Z",
        run_id="test-run",
        findings=[
            generator.Finding(
                topic="<script>alert(1)</script>",
                summary="<img src=x onerror=alert(1)>",
                sources=[
                    {"url": "javascript:alert(1)", "title": "unsafe"},
                    {"url": "https://example.test/?a=1&b=2", "title": "<b>source</b>"},
                ],
                gaps=["<svg onload=alert(1)>"],
                discrepancies=[],
                confidence=0.8,
                generated_at="2026-10-23T00:00:00Z",
            )
        ],
    )

    html = generator.generate_index_html(report)

    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "<svg" not in html
    assert "javascript:" not in html
    assert "&lt;b&gt;source&lt;/b&gt;" in html
    assert "https://example.test/?a=1&amp;b=2" in html


def test_write_report_persists_current_and_history_snapshots(tmp_path, monkeypatch):
    report_dir = tmp_path / "infogap"
    report = generator.Report(
        generated_at="2026-10-23T00:00:00Z",
        run_id="test-run",
    )
    monkeypatch.setattr(generator, "REPORT_JSON", report_dir / "report.json")
    monkeypatch.setattr(generator, "HISTORY_DIR", report_dir / "history")

    generator.write_report(report)

    current = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    history = json.loads(
        (report_dir / "history" / "2026-10-23.json").read_text(encoding="utf-8")
    )
    assert current == history
    assert current["run_id"] == "test-run"


def test_build_report_records_selected_topics():
    report = generator.build_report([], topics=["selected topic"])

    assert report.topics == ["selected topic"]


def test_run_last30days_research_uses_upstream_script_and_normalizes_export(tmp_path, monkeypatch):
    script = tmp_path / "last30days.py"
    script.touch()
    monkeypatch.setenv("LAST30DAYS_SCRIPT", str(script))
    upstream_export = {
        "query": "selected topic",
        "clusters": [{"summary": "Cluster summary", "sources": ["reddit"]}],
        "results": [
            {
                "title": "Result title",
                "url": "https://example.test/result",
                "summary": "Result summary",
            }
        ],
    }
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(upstream_export),
            stderr="",
        )

    monkeypatch.setattr(generator.subprocess, "run", fake_run)

    success, data = generator.run_last30days_research("selected topic", tmp_path / "save")

    assert success
    assert calls[0][0] == [
        sys.executable,
        str(script),
        "selected topic",
        "--research-only",
        "--save-dir",
        str(tmp_path / "save"),
        "--emit=json",
    ]
    assert data["sources"] == [
        {"url": "https://example.test/result", "title": "Result title"}
    ]
    assert data["findings"] == upstream_export["results"]
    assert data["summary"] == "Cluster summary"
