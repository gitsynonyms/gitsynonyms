import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "monitoring" / "upload_mirrors.py"
SPEC = importlib.util.spec_from_file_location("upload_mirrors", SCRIPT)
upload_mirrors = importlib.util.module_from_spec(SPEC)
sys.modules["upload_mirrors"] = upload_mirrors
SPEC.loader.exec_module(upload_mirrors)


class FakeCurl:
    def __init__(self, failing=()):
        self.calls = []
        self.failing = set(failing)

    def __call__(self, args, timeout=180.0):
        self.calls.append(list(args))
        joined = " ".join(args)
        for name in self.failing:
            if name in joined:
                raise RuntimeError(f"{name} unavailable")
        if "%{http_code} %{size_download}" in args:
            return 0, b"206 1024", 0.5
        if "api.gofile.io/servers" in joined:
            return 0, json.dumps({"data": {"servers": [{"name": "store1"}]}}).encode(), 0.2
        if "gofile.io/contents/uploadfile" in joined:
            return 0, json.dumps({"data": {"downloadPage": "https://gofile.io/d/abc"}}).encode(), 1.0
        if "tmpfiles.org/api/v1/upload" in joined:
            return 0, json.dumps({"data": {"url": "https://tmpfiles.org/1/x.txt"}}).encode(), 0.3
        if "uguu.se" in joined:
            return 0, json.dumps({"files": [{"url": "https://d.uguu.se/x.txt"}]}).encode(), 0.4
        if "temp.sh" in joined:
            return 0, b"https://temp.sh/abc/x.txt\n", 0.5
        if "x0.at" in joined:
            return 0, b"https://x0.at/abc.txt\n", 0.6
        if "qu.ax" in joined:
            return 0, json.dumps({"files": [{"url": "https://qu.ax/abc"}]}).encode(), 0.7
        if "pomf.lain.la" in joined:
            return 0, json.dumps({"files": [{"url": "https://pomf2.lain.la/f/abc.txt"}]}).encode(), 0.8
        if "filebin.net" in joined:
            return 0, b"", 0.9
        raise AssertionError(f"unexpected curl call: {joined}")


def test_upload_artifacts_covers_every_configured_mirror(tmp_path):
    artifact = tmp_path / "bilingual.srt"
    artifact.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    run = FakeCurl()

    payload = upload_mirrors.upload_artifacts(
        [artifact], filebin_bin="https://filebin.net/bin123", run=run, quiet=True
    )

    assert payload["summary"] == {"attempted": len(upload_mirrors.MIRRORS), "ok": len(upload_mirrors.MIRRORS)}
    rows = payload["artifacts"][0]["uploads"]
    by_name = {row["mirror"]: row for row in rows}
    assert by_name["filebin.net"]["url"] == "https://filebin.net/bin123/bilingual.srt"
    assert by_name["filebin.net"]["download"]["bytes_per_second"] == 2048.0
    assert by_name["filebin.net"]["upload_bytes_per_second"] > 0


def test_upload_artifacts_records_a_failing_mirror_without_aborting(tmp_path):
    artifact = tmp_path / "a.srt"
    artifact.write_bytes(b"x" * 100)
    payload = upload_mirrors.upload_artifacts(
        [artifact],
        only=["filebin.net"],
        run=FakeCurl(failing={"filebin.net"}),
        quiet=True,
    )
    assert payload["summary"] == {"attempted": 1, "ok": 0}
    failed = [row for row in payload["artifacts"][0]["uploads"] if row["status"] == "error"]
    assert failed and "unavailable" in failed[0]["error"]


def test_measure_download_reports_throughput():
    result = upload_mirrors.measure_download("https://example.test/f", run=FakeCurl())
    assert result == {
        "http_status": 206,
        "bytes": 1024,
        "seconds": 0.5,
        "bytes_per_second": 2048.0,
    }


def test_render_markdown_lists_links_speeds_and_limits(tmp_path):
    artifact = tmp_path / "a.srt"
    artifact.write_bytes(b"x" * 100)
    payload = upload_mirrors.upload_artifacts(
        [artifact], run=FakeCurl(), quiet=True
    )
    report = upload_mirrors.render_markdown(payload)
    assert "网盘镜像上传报告" in report
    assert "https://filebin.net/f8l40pcd28jo8bl0/a.srt" in report
    assert "KB/s" in report
    assert "2 GB" in report


def test_human_rate_switches_units():
    assert upload_mirrors.human_rate(512) == "512 B/s"
    assert upload_mirrors.human_rate(2048) == "2.0 KB/s"
    assert upload_mirrors.human_rate(5 * 1024 * 1024) == "5.00 MB/s"
