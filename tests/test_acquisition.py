import importlib.util
import io
import json
import sys
import tempfile
import threading
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "monitoring" / "acquisition.py"
SPEC = importlib.util.spec_from_file_location("acquisition", SCRIPT)
acquisition = importlib.util.module_from_spec(SPEC)
sys.modules["acquisition"] = acquisition
SPEC.loader.exec_module(acquisition)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
ARTICLE_BODY = (
    "The President delivered remarks about trade and manufacturing in the Rose "
    "Garden on Thursday afternoon, outlining tariffs, new investment commitments "
    "and the administration's expectations for the coming negotiations with "
    "trading partners in Europe and Asia."
)

RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Fixture feed</title>
    <link>https://example.gov/news/</link>
    <item>
      <title>President delivers remarks on trade</title>
      <link>https://example.gov/news/trade?utm_source=rss&amp;utm_medium=feed</link>
      <pubDate>Thu, 22 Oct 2026 20:00:00 +0000</pubDate>
      <description><![CDATA[<p>{ARTICLE_BODY}</p>]]></description>
    </item>
    <item>
      <title>Old proclamation from another year</title>
      <link>https://example.gov/news/old</link>
      <pubDate>Thu, 22 Oct 2020 20:00:00 +0000</pubDate>
      <description><![CDATA[<p>{ARTICLE_BODY}</p>]]></description>
    </item>
    <item>
      <title>Headline with no summary</title>
      <link>https://example.gov/news/thin</link>
      <pubDate>Thu, 22 Oct 2026 21:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom fixture</title>
  <entry>
    <title>Briefing by the Press Secretary</title>
    <link rel="alternate" href="https://example.gov/briefing"/>
    <updated>2026-10-22T18:30:00Z</updated>
    <summary type="html">&lt;p&gt;The Press Secretary answered questions about trade, the budget and the schedule for the coming week.&lt;/p&gt;</summary>
  </entry>
</feed>
"""

GDELT = {
    "articles": [
        {
            "title": "White House signals new tariff talks",
            "url": "https://news.example.com/tariff-talks?utm_campaign=daily",
            "seendate": "20261022T191500Z",
            "domain": "news.example.com",
            "language": "English",
            "sourcecountry": "United States",
        },
        {
            "title": "Analysts weigh in on the trade agenda",
            "url": "https://news.example.com/trade-analysis",
            "seendate": "20261022T173000Z",
            "domain": "news.example.com",
            "language": "Spanish",
            "sourcecountry": "Spain",
        },
    ]
}

PAGE = (
    "<!doctype html><html lang='en'><head><title>Trade remarks</title></head>"
    "<body><div class='site-nav'>Menu that should be ignored</div>"
    "<svg><title>RollCall logo</title></svg><script>var track = 1;</script>"
    f"<main><article><h1>Trade remarks</h1><p>{ARTICLE_BODY}</p>"
    f"<p>{ARTICLE_BODY}</p></article></main></body></html>"
)


class _FixtureHandler(BaseHTTPRequestHandler):
    routes = {}
    hits = {}

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0]
        hits = type(self).hits
        hits[path] = hits.get(path, 0) + 1
        route = type(self).routes.get(path)
        if route is None:
            body = b"missing"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if callable(route):
            route = route(hits[path])
        payload = route.get("body", "")
        body = payload.encode("utf-8") if isinstance(payload, str) else payload
        etag = route.get("etag")
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(route.get("status", 200))
        self.send_header(
            "Content-Type", route.get("content_type", "application/octet-stream")
        )
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def serve(routes):
    _FixtureHandler.routes = routes
    _FixtureHandler.hits = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def feed_routes(etag="v1"):
    return {
        "/feed.xml": {"body": RSS, "content_type": "application/rss+xml", "etag": etag},
        "/atom.xml": {"body": ATOM, "content_type": "application/atom+xml"},
        "/page": {"body": PAGE, "content_type": "text/html; charset=utf-8"},
        "/gdelt.json": {"body": json.dumps(GDELT), "content_type": "application/json"},
        "/file.bin": {"body": b"material-bytes", "content_type": "application/octet-stream"},
    }


def spec(name, kind, url, trust="official", language="en", parser=""):
    return acquisition.SourceSpec(
        name=name, kind=kind, url=url, trust=trust, language=language, parser=parser
    )


def run_collect(out, sources, *, since=None, until=None, limit=10, dry_run=False, **kwargs):
    kwargs.setdefault("sleep", lambda _: None)
    run = acquisition.Acquisition(out, sources=sources, now=NOW, **kwargs)
    return run.collect(since=since, until=until, limit=limit, dry_run=dry_run)


def records_by_title(manifest):
    return {record["title"]: record for record in manifest["materials"]}


def fake_youtube(specification, limit):
    return [
        {
            "id": "5YerQHGsX04",
            "title": "President Trump Gaggles with Press Upon Departure",
            "upload_date": "20261022",
            "duration": 620,
            "live_status": "not_live",
        },
        {
            "id": "AAAAAAAAAAA",
            "title": "Live stream right now",
            "upload_date": "20261022",
            "duration": 30,
            "live_status": "is_live",
        },
    ]


# ---------------------------------------------------------------------------
# Identity, parsing and grading
# ---------------------------------------------------------------------------


def test_canonical_url_drops_tracking_and_normalises():
    assert (
        acquisition.canonical_url(
            "https://WWW.Example.GOV/news/item/?utm_source=x&b=2&a=1#top"
        )
        == "https://example.gov/news/item?a=1&b=2"
    )
    assert (
        acquisition.canonical_url("https://youtu.be/5YerQHGsX04?t=30")
        == "https://youtube.com/watch?v=5YerQHGsX04"
    )
    assert acquisition.canonical_url("not a url") == ""


def test_event_key_keeps_one_youtube_identity():
    assert acquisition.event_key(
        "https://www.youtube.com/watch?v=5YerQHGsX04&t=10"
    ) == acquisition.event_key("https://youtu.be/5YerQHGsX04")
    assert acquisition.event_key("https://example.gov/news/trade?utm_source=rss") == (
        "url:https://example.gov/news/trade"
    )


def test_parse_feed_reads_rss_and_atom():
    entries = acquisition.parse_feed(RSS)
    assert [entry.title for entry in entries][:2] == [
        "President delivers remarks on trade",
        "Old proclamation from another year",
    ]
    assert entries[0].published == "2026-10-22T20:00:00Z"
    assert "Rose Garden" in entries[0].summary
    atom = acquisition.parse_feed(ATOM)
    assert atom[0].link == "https://example.gov/briefing"
    assert atom[0].published == "2026-10-22T18:30:00Z"


def test_parse_gdelt_maps_language_and_time():
    entries = acquisition.parse_gdelt(GDELT)
    assert entries[0].published == "2026-10-22T19:15:00Z"
    assert entries[0].language == "en"
    assert entries[1].language == "es"


def test_extract_readable_prefers_the_main_region():
    page = acquisition.extract_readable(PAGE)
    assert page["title"] == "Trade remarks"
    assert "Menu that should be ignored" not in page["text"]
    assert "Rose Garden" in page["text"]
    fallback = acquisition.extract_readable(
        "<html><body><div>Short nav</div><p>Only a short body.</p></body></html>"
    )
    assert "Only a short body." in fallback["text"]


def test_source_region_hint_beats_page_chrome():
    page = (
        "<html><body><div class='nav'>Menu that should be ignored</div>"
        f"<div id='calendar-results'><p>{ARTICLE_BODY}</p><p>{ARTICLE_BODY}</p></div>"
        "<footer>Footer chrome</footer></body></html>"
    )
    hinted = acquisition.extract_readable(page, ("calendar-results",))
    assert "Rose Garden" in hinted["text"]
    assert "Menu that should be ignored" not in hinted["text"]
    assert "Footer chrome" not in hinted["text"]
    unhinted = acquisition.extract_readable(page)
    assert "Menu that should be ignored" in unhinted["text"]


def test_quality_grades_payload_reference_and_rejection():
    summary = "A sufficiently long summary about the President's trade remarks."
    entry = acquisition.Material(
        source="fixture",
        kind="article",
        url="https://example.gov/news/trade",
        title="Trade remarks",
        published_at="2026-10-22T20:00:00Z",
        byte_size=len(summary),
        sha256=acquisition.sha256_text(summary),
        details={"capture": "entry", "text_chars": len(summary)},
    ).finalize()
    assert entry.grade == "B"
    assert entry.quality["checks"]["payload"] is False

    page = acquisition.Material(
        source="fixture",
        kind="article",
        url="https://example.gov/news/trade",
        title="Trade remarks",
        published_at="2026-10-22T20:00:00Z",
        byte_size=len(ARTICLE_BODY),
        sha256=acquisition.sha256_text(ARTICLE_BODY),
        details={"capture": "page", "text_chars": len(ARTICLE_BODY)},
    ).finalize()
    assert page.grade == "A"

    thin = acquisition.Material(
        source="fixture",
        kind="article",
        url="https://example.gov/news/thin",
        title="Headline only",
        published_at="2026-10-22T21:00:00Z",
        byte_size=5,
        sha256=acquisition.sha256_text("headline"),
        details={"capture": "entry", "text_chars": 5},
    ).finalize()
    assert thin.grade == "C"
    assert thin.status == "rejected"
    assert "content" in thin.quality["reasons"]

    video = acquisition.Material(
        source="whitehouse-youtube",
        kind="video",
        url="https://www.youtube.com/watch?v=5YerQHGsX04",
        title="Gaggle",
        published_at="2026-10-22T20:00:00Z",
        sha256=acquisition.sha256_text("video"),
        details={"capture": "reference", "duration": 620, "text_chars": 6},
    ).finalize()
    assert video.grade == "B"
    assert video.event_key == "yt:5YerQHGsX04"

    asset = acquisition.Material(
        source="fixture",
        kind="asset",
        url="https://example.gov/file.bin",
        title="file",
        byte_size=14,
        sha256=acquisition.sha256_text("material-bytes"),
        details={"capture": "file", "text_chars": 0},
    ).finalize()
    assert asset.grade == "A"


# ---------------------------------------------------------------------------
# End-to-end collection
# ---------------------------------------------------------------------------


def test_collect_writes_ledger_and_manifest():
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes(etag=None)) as base:
        sources = [
            spec("feed", "rss", base + "/feed.xml"),
            spec("wire", "json", base + "/gdelt.json", trust="aggregator", parser="gdelt"),
            spec("calendar", "html", base + "/page", trust="wire"),
            spec("channel", "youtube", base + "/feed.xml"),
        ]
        manifest = run_collect(
            tmp, sources, since=NOW - timedelta(days=2), youtube_lister=fake_youtube
        )
        titles = records_by_title(manifest)
        assert manifest["schema"] == acquisition.SCHEMA
        assert manifest["counts"]["materials"] == 6
        assert manifest["counts"]["this_run"]["new"] == 6
        assert "Old proclamation from another year" not in titles
        assert titles["Headline with no summary"]["status"] == "rejected"
        assert titles["President delivers remarks on trade"]["grade"] == "B"
        page = titles["Trade remarks"]
        assert page["grade"] == "A"
        assert page["details"]["capture"] == "page"
        assert "Menu that should be ignored" not in page["details"]["text"]
        video = titles["President Trump Gaggles with Press Upon Departure"]
        assert video["kind"] == "video" and video["grade"] == "B"
        assert "Live stream right now" not in titles
        ledger = [
            json.loads(line)
            for line in Path(tmp, "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(ledger) == 6
        assert len({record["material_id"] for record in ledger}) == 6
        assert Path(tmp, "manifest.json").exists()
        assert Path(tmp, ".http-cache.json").exists()


def test_same_story_from_two_sources_is_corroboration_not_duplicate():
    routes = feed_routes(etag=None)
    routes["/feed2.xml"] = {
        "body": RSS.replace("<title>Fixture feed</title>", "<title>Second feed</title>"),
        "content_type": "application/rss+xml",
    }
    with tempfile.TemporaryDirectory() as tmp, serve(routes) as base:
        sources = [
            spec("official", "rss", base + "/feed.xml"),
            spec("wire", "rss", base + "/feed2.xml", trust="wire"),
        ]
        manifest = run_collect(tmp, sources, since=NOW - timedelta(days=2))
        matching = [
            record
            for record in manifest["materials"]
            if record["title"] == "President delivers remarks on trade"
        ]
        assert len(matching) == 1
        record = matching[0]
        assert record["source"] == "official"
        assert [item["source"] for item in record["details"]["also_seen_in"]] == ["wire"]
        event = next(
            item
            for item in manifest["events"]
            if item["event_key"] == "url:https://example.gov/news/trade"
        )
        assert event["corroborated"] is True
        assert event["corroboration"] == 2
        assert event["trust_score"] == 1.0


def test_second_run_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes(etag=None)) as base:
        sources = [spec("feed", "rss", base + "/feed.xml")]
        first = run_collect(tmp, sources, since=NOW - timedelta(days=2))
        ledger_before = Path(tmp, "ledger.jsonl").read_text(encoding="utf-8")
        second = run_collect(tmp, sources, since=NOW - timedelta(days=2))
        assert Path(tmp, "ledger.jsonl").read_text(encoding="utf-8") == ledger_before
        assert second["counts"]["this_run"]["unchanged"] == first["counts"]["materials"]
        assert second["counts"]["this_run"]["new"] == 0
        assert second["counts"]["materials"] == first["counts"]["materials"]


def test_changed_content_appends_a_revision():
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes(etag=None)) as base:
        sources = [spec("feed", "rss", base + "/feed.xml")]
        first = run_collect(tmp, sources, since=NOW - timedelta(days=2))
        before = records_by_title(first)["President delivers remarks on trade"]
        updated = RSS.replace("outlining tariffs", "outlining new tariffs and quotas")
        _FixtureHandler.routes["/feed.xml"] = {
            "body": updated,
            "content_type": "application/rss+xml",
        }
        second = run_collect(tmp, sources, since=NOW - timedelta(days=2))
        after = records_by_title(second)["President delivers remarks on trade"]
        assert after["revision"] == before["revision"] + 1
        assert after["sha256"] != before["sha256"]
        assert "quotas" in after["details"]["summary"]
        lines = Path(tmp, "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == first["counts"]["materials"] + 1


def test_conditional_request_reports_not_modified():
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes(etag="v1")) as base:
        sources = [spec("feed", "rss", base + "/feed.xml")]
        first = run_collect(tmp, sources, since=NOW - timedelta(days=2))
        second = run_collect(tmp, sources, since=NOW - timedelta(days=2))
        assert second["sources"][0]["status"] == "unchanged"
        assert second["counts"]["this_run"]["materials"] == 0
        assert second["counts"]["materials"] == first["counts"]["materials"]


def test_fetch_articles_upgrades_entries_to_full_text():
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes(etag=None)) as base:
        _FixtureHandler.routes["/news/trade"] = {
            "body": PAGE,
            "content_type": "text/html; charset=utf-8",
        }
        _FixtureHandler.routes["/feed.xml"] = {
            "body": RSS.replace("https://example.gov", base),
            "content_type": "application/rss+xml",
        }
        manifest = run_collect(
            tmp,
            [spec("feed", "rss", base + "/feed.xml")],
            since=NOW - timedelta(days=2),
            fetch_articles=True,
        )
        record = records_by_title(manifest)["President delivers remarks on trade"]
        assert record["grade"] == "A"
        assert record["details"]["capture"] == "page"
        assert "Rose Garden" in record["details"]["text"]
        assert "Menu that should be ignored" not in record["details"]["text"]


def test_dead_source_degrades_without_stopping_the_run():
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes(etag=None)) as base:
        sources = [
            spec("feed", "rss", base + "/feed.xml"),
            spec("gone", "rss", base + "/missing.xml"),
            spec("channel", "youtube", base + "/feed.xml"),
        ]

        def broken_youtube(specification, limit):
            raise ImportError("yt-dlp is not installed")

        manifest = run_collect(
            tmp, sources, since=NOW - timedelta(days=2), youtube_lister=broken_youtube
        )
        statuses = {source["name"]: source["status"] for source in manifest["sources"]}
        assert statuses["feed"] == "ok"
        assert statuses["gone"] == "unavailable"
        assert statuses["channel"] == "unavailable"
        assert manifest["counts"]["materials"] >= 2
        gone = next(source for source in manifest["sources"] if source["name"] == "gone")
        assert "404" in gone["note"]
        assert "yt-dlp" in next(
            source for source in manifest["sources"] if source["name"] == "channel"
        )["note"]


def test_retry_recovers_from_a_transient_server_error():
    routes = {
        "/flaky.xml": lambda hit: (
            {"body": RSS, "content_type": "application/rss+xml"}
            if hit > 1
            else {"status": 500, "body": "boom"}
        )
    }
    with serve(routes) as base:
        fetcher = acquisition.HttpFetcher(retries=1, sleep=lambda _: None)
        result = fetcher.get(base + "/flaky.xml")
        assert result.status == "ok"
        assert "Fixture feed" in result.body.decode("utf-8")
        assert _FixtureHandler.hits["/flaky.xml"] == 2


def test_asset_download_is_named_by_checksum():
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes()) as base:
        manifest = run_collect(
            tmp,
            [spec("attachment", "asset", base + "/file.bin")],
            download=True,
        )
        record = manifest["materials"][0]
        assert record["grade"] == "A"
        stored = Path(tmp, record["local_path"])
        assert stored.read_bytes() == b"material-bytes"
        assert stored.name.startswith(
            acquisition.sha256_text("material-bytes")[:16]
        )
        assert Path(record["local_path"]).parts[0] == "assets"


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes(etag=None)) as base:
        manifest = run_collect(
            tmp,
            [spec("feed", "rss", base + "/feed.xml")],
            since=NOW - timedelta(days=2),
            dry_run=True,
        )
        assert manifest["counts"]["materials"] >= 1
        assert list(Path(tmp).iterdir()) == []


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------


def test_cli_collect_json_then_report():
    original = acquisition.SOURCES
    with tempfile.TemporaryDirectory() as tmp, serve(feed_routes(etag=None)) as base:
        acquisition.SOURCES = (spec("feed", "rss", base + "/feed.xml"),)
        try:
            collected = io.StringIO()
            with redirect_stdout(collected):
                code = acquisition.main(
                    [
                        "collect",
                        "--out",
                        tmp,
                        "--since",
                        "2026-10-20T00:00:00Z",
                        "--limit",
                        "5",
                        "--json",
                    ]
                )
            assert code == 0
            payload = json.loads(collected.getvalue())
            assert payload["schema"] == acquisition.SCHEMA
            assert payload["counts"]["materials"] >= 1
            assert payload["sources"][0]["name"] == "feed"

            rendered = io.StringIO()
            with redirect_stdout(rendered):
                assert acquisition.main(["report", "--out", tmp, "--json"]) == 0
            summary = json.loads(rendered.getvalue())
            assert summary["counts"]["materials"] == payload["counts"]["materials"]
            assert summary["events"]
        finally:
            acquisition.SOURCES = original


def test_cli_exit_code_marks_a_total_outage():
    original = acquisition.SOURCES
    with tempfile.TemporaryDirectory() as tmp, serve({}) as base:
        acquisition.SOURCES = (spec("gone", "rss", base + "/missing.xml"),)
        try:
            with redirect_stdout(io.StringIO()):
                code = acquisition.main(
                    ["collect", "--out", tmp, "--quiet", "--retries", "0"]
                )
            assert code == 1
        finally:
            acquisition.SOURCES = original


def test_report_without_a_manifest_exits_with_usage_error():
    with tempfile.TemporaryDirectory() as tmp:
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            assert acquisition.main(["report", "--out", tmp]) == 2
        assert "no manifest" in stderr.getvalue()


def test_cli_rejects_an_unknown_source_name():
    with tempfile.TemporaryDirectory() as tmp:
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = acquisition.main(
                ["collect", "--out", tmp, "--sources", "whitehouse-news,nope"]
            )
        assert code == 2
        assert "unknown source(s): nope" in stderr.getvalue()
        assert not Path(tmp, "manifest.json").exists()





