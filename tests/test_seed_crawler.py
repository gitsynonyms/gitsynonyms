import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "crawl_seed_graph.py"
SPEC = importlib.util.spec_from_file_location("crawl_seed_graph", SCRIPT)
crawler = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = crawler
SPEC.loader.exec_module(crawler)


class FakeFetcher:
    """Offline fetcher: pages maps a URL to an HTML/text body."""

    def __init__(self, pages, robots=None):
        self.pages = pages
        self.robots = robots or {}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            origin = url[: -len("/robots.txt")]
            body = self.robots.get(origin)
            if body is None:
                return crawler.FetchResult(status=404, error="HTTP 404")
            return crawler.FetchResult(
                status=200, content_type="text/plain", text=body, final_url=url
            )
        body = self.pages.get(url)
        if body is None:
            return crawler.FetchResult(status=404, error="HTTP 404")
        return crawler.FetchResult(
            status=200, content_type="text/html", text=body, final_url=url
        )


def query(db, sql, params=()):
    connection = sqlite3.connect(str(db))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()


def run_crawl(db, pages, **kwargs):
    kwargs.setdefault("max_depth", 10)
    kwargs.setdefault("max_pages", 50)
    kwargs.setdefault("workers", 2)
    kwargs.setdefault("progress", lambda _: None)
    seeds = kwargs.pop("seeds", ["https://a.test/"])
    robots = kwargs.pop("robots", None)
    return crawler.crawl(
        seeds, db, fetcher=FakeFetcher(pages, robots), **kwargs
    )


def test_extract_page_collects_links_and_proxies():
    page = crawler.extract_page(
        "<html><title> Proxy List </title>"
        '<a href="/a">A</a><a href="b.txt">B</a>'
        " 1.2.3.4:8080 socks5://5.6.7.8:1080 999.1.1.1:80",
        "https://example.com/root/index.html",
        "text/html",
    )
    assert page.title == "Proxy List"
    assert "https://example.com/a" in page.links
    assert "https://example.com/root/b.txt" in page.links
    assert ("http", "1.2.3.4", 8080) in page.proxies
    assert ("socks5", "5.6.7.8", 1080) in page.proxies
    assert ("http", "999.1.1.1", 80) not in page.proxies


def test_normalize_url_drops_fragments_and_default_ports():
    assert (
        crawler.normalize_url("https://a.test/x", "../y?q=1#z")
        == "https://a.test/y?q=1"
    )
    assert (
        crawler.normalize_url("https://a.test/", "https://a.test:443/p")
        == "https://a.test/p"
    )
    assert crawler.normalize_url("https://a.test/", "mailto:x@y.test") is None
    assert crawler.normalize_url("https://a.test/", "javascript:void(0)") is None


def test_crawl_assigns_levels_until_no_new_links(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": '<a href="/b">b</a><a href="https://c.test/c">c</a> 1.2.3.4:8080',
        "https://a.test/b": '<a href="https://d.test/">d</a>',
        "https://c.test/c": '<a href="https://a.test/b#again">b</a>',
        "https://d.test/": '<a href="https://a.test/">home</a>',
    }
    summary = run_crawl(db, pages)

    assert summary["converged"] is True
    assert summary["stop_reason"] == "converged"
    assert summary["max_level"] == 3
    levels = {row["url"]: row["level"] for row in query(db, "SELECT url, level FROM nodes")}
    assert levels == {
        "https://a.test/": 1,
        "https://a.test/b": 2,
        "https://c.test/c": 2,
        "https://d.test/": 3,
    }
    assert query(db, "SELECT scheme, host, port, level FROM proxies") == [
        {"scheme": "http", "host": "1.2.3.4", "port": 8080, "level": 1}
    ]


def test_crawl_stops_when_every_link_is_already_known(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": '<a href="https://a.test/b">b</a>',
        "https://a.test/b": '<a href="https://a.test/">home</a>',
    }
    summary = run_crawl(db, pages)
    assert summary["stop_reason"] == "converged"
    assert summary["max_level"] == 2
    assert len(query(db, "SELECT url FROM nodes")) == 2


def test_max_depth_stops_before_queueing_the_next_level(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": '<a href="https://b.test/">b</a>',
        "https://b.test/": '<a href="https://c.test/">c</a>',
    }
    summary = run_crawl(db, pages, max_depth=2)
    assert summary["stop_reason"] == "max-depth"
    urls = {row["url"] for row in query(db, "SELECT url FROM nodes")}
    assert urls == {"https://a.test/", "https://b.test/"}


def test_rerun_and_refresh_do_not_duplicate_rows(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": '<a href="https://a.test/b">b</a> 1.2.3.4:8080',
        "https://a.test/b": '<a href="https://a.test/">home</a>',
    }
    run_crawl(db, pages)
    before = query(db, "SELECT url FROM nodes ORDER BY url")
    again = run_crawl(db, pages)
    assert again["pages_fetched"] == 0
    assert again["stop_reason"] == "converged"
    assert query(db, "SELECT url FROM nodes ORDER BY url") == before

    refreshed = run_crawl(db, pages, refresh=True)
    assert refreshed["pages_fetched"] == 1
    assert query(db, "SELECT url FROM nodes ORDER BY url") == before
    assert len(query(db, "SELECT * FROM proxies")) == 1


def test_asset_and_ip_links_are_recorded_but_not_fetched(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": (
            '<a href="/image.png">image</a>'
            '<a href="http://1.2.3.4:8080/">proxy</a>'
            '<a href="/list.txt">list</a>'
        ),
        "https://a.test/list.txt": "5.6.7.8:3128",
    }
    run_crawl(db, pages)
    urls = {row["url"]: row["level"] for row in query(db, "SELECT url, level FROM nodes")}
    assert "https://a.test/image.png" not in urls
    assert "http://1.2.3.4:8080/" not in urls
    assert urls["https://a.test/list.txt"] == 2
    proxies = {(row["scheme"], row["host"]) for row in query(db, "SELECT * FROM proxies")}
    assert ("http", "1.2.3.4") in proxies
    assert ("http", "5.6.7.8") in proxies


def test_robots_txt_blocks_a_link(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": '<a href="/private/x">x</a><a href="/public">p</a>',
        "https://a.test/public": "ok",
    }
    summary = run_crawl(
        db,
        pages,
        robots={"https://a.test": "User-agent: *\nDisallow: /private"},
    )
    statuses = {
        row["url"]: row["status"] for row in query(db, "SELECT url, status FROM nodes")
    }
    assert statuses["https://a.test/private/x"] == "skipped"
    assert statuses["https://a.test/public"] == "done"
    assert summary["stop_reason"] == "converged"


def test_allow_and_deny_regex_filter_links(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": (
            '<a href="https://a.test/keep/1">1</a>'
            '<a href="https://a.test/drop/2">2</a>'
        ),
    }
    run_crawl(db, pages, allow_pattern=r"/keep/", deny_pattern=r"/drop/")
    urls = {row["url"] for row in query(db, "SELECT url FROM nodes")}
    assert "https://a.test/keep/1" in urls
    assert "https://a.test/drop/2" not in urls


def test_export_nodes_labels_seed_and_backlink(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": '<a href="https://a.test/b">b</a>',
        "https://a.test/b": "done",
    }
    run_crawl(db, pages)
    output = tmp_path / "nodes.jsonl"
    assert crawler.export_nodes(db, output) == 2
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    seed = next(record for record in records if record["url"] == "https://a.test/")
    link = next(record for record in records if record["url"] == "https://a.test/b")
    assert (seed["role"], seed["hop"], seed["level"]) == ("seed", 0, 1)
    assert (link["role"], link["hop"], link["level"]) == ("backlink", 1, 2)


def test_export_proxies_writes_scheme_host_port(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {"https://a.test/": "1.2.3.4:8080 socks5://5.6.7.8:1080"}
    run_crawl(db, pages)
    output = tmp_path / "proxies.txt"
    assert crawler.export_proxies(db, output) == 2
    assert output.read_text(encoding="utf-8").splitlines() == [
        "http://1.2.3.4:8080",
        "socks5://5.6.7.8:1080",
    ]


def test_report_summarizes_levels_without_changing_them(tmp_path):
    db = tmp_path / "graph.sqlite3"
    pages = {
        "https://a.test/": '<a href="https://b.test/">b</a> 1.2.3.4:8080',
        "https://b.test/": "done",
    }
    run_crawl(db, pages)
    stored = crawler.report(db)
    assert stored["totals"] == {
        "nodes": 2,
        "seeds": 1,
        "links": 1,
        "proxies": 1,
        "edges": 1,
    }
    assert stored["levels"][0]["level"] == 1
    assert stored["levels"][0]["proxies"] == 1
    assert stored["meta"]["converged"] == "yes"


def test_cli_runs_against_a_local_http_server(tmp_path):
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        pages = {
            "/": b'<a href="/next">next</a> 1.2.3.4:8080',
            "/next": b"done",
        }

        def do_GET(self):
            if self.path == "/robots.txt":
                self.send_response(404)
                self.end_headers()
                return
            body = self.pages.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/"
        db = tmp_path / "graph.sqlite3"
        assert crawler.main(
            ["--seed", base, "--db", str(db), "--max-depth", "3", "--allow-ip"]
        ) == 0
        urls = {row["url"]: row["level"] for row in query(db, "SELECT url, level FROM nodes")}
        assert urls[base] == 1
        assert urls[base + "next"] == 2
    finally:
        server.shutdown()
        server.server_close()

