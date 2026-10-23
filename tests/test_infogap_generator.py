import importlib.util
import json
import sys
from pathlib import Path

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
