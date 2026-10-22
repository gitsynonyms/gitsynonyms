import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "find_backlinks.py"
SPEC = importlib.util.spec_from_file_location("find_backlinks", SCRIPT)
backlinks = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backlinks
SPEC.loader.exec_module(backlinks)


class FakeFetcher:
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        body = self.pages.get(url)
        if body is None:
            return backlinks.FetchResult(status=404, error="HTTP 404")
        return backlinks.FetchResult(
            status=200, content_type="text/html", text=body, final_url=url
        )


def fake_provider(name, mapping):
    def provider(target, options):
        return backlinks.ProviderResult(
            name,
            [
                entry
                if isinstance(entry, backlinks.Backlink)
                else backlinks.Backlink(entry, "")
                for entry in mapping.get(target, [])
            ],
        )

    return provider


def run_crawl(db, mapping, *, pages=None, registry=None, **kwargs):
    kwargs.setdefault("max_depth", 10)
    kwargs.setdefault("max_queries", 50)
    kwargs.setdefault("max_pages", 200)
    kwargs.setdefault("progress", lambda _: None)
    providers = kwargs.pop("providers", ["fake"])
    registry = registry or {"fake": fake_provider("fake", mapping)}
    options = backlinks.ProviderOptions(
        fetcher=FakeFetcher(pages), limiter=backlinks.RateLimiter(0), workers=2
    )
    return backlinks.crawl(
        kwargs.pop("seeds", ["https://s.test/"]),
        db,
        providers=providers,
        registry=registry,
        options=options,
        **kwargs,
    )


def query(db, sql, params=()):
    connection = sqlite3.connect(str(db))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()


def test_level_labels_use_chinese_generations():
    assert backlinks.level_label(1, "seed") == "一级种子"
    assert backlinks.level_label(2, "seed") == "二级种子"
    assert backlinks.level_label(2, "backlink") == "一级反向链接"
    assert backlinks.level_label(3, "backlink") == "二级反向链接"
    assert backlinks.level_label(11, "backlink") == "十级反向链接"


def test_same_target_ignores_scheme_www_and_trailing_slash():
    assert backlinks.same_target("http://example.com/a/", "https://www.example.com/a")
    assert not backlinks.same_target("https://example.com/a", "https://example.com/b")
    assert not backlinks.same_target(
        "https://example.com/a?x=1", "https://example.com/a?x=2"
    )


def test_mentions_and_link_matches():
    target = "https://www.example.com/release/2026/"
    assert backlinks._mentions(f"see {target} for details", target)
    assert backlinks._mentions("see www.example.com/release/2026/", target)
    assert not backlinks._mentions("nothing here", target)
    assert backlinks._link_matches("https://example.com/release/2026", target)
    assert not backlinks._link_matches("https://example.com/other", target)


def test_crawl_assigns_backlink_levels_until_closure(tmp_path):
    db = tmp_path / "graph.sqlite3"
    mapping = {
        "https://s.test/": ["https://b.test/post", "https://c.test/"],
        "https://b.test/post": ["https://d.test/"],
        "https://c.test/": [],
        "https://d.test/": ["https://b.test/post#again"],
    }
    summary = run_crawl(db, mapping, verify=False)

    assert summary["converged"] is True
    assert summary["stop_reason"] == "converged"
    assert summary["max_level"] == 3
    levels = {
        row["url"]: (row["level"], row["relation"])
        for row in query(db, "SELECT url, level, relation FROM nodes")
    }
    assert levels == {
        "https://s.test/": (1, "seed"),
        "https://b.test/post": (2, "mention"),
        "https://c.test/": (2, "mention"),
        "https://d.test/": (3, "mention"),
    }
    edges = query(db, "SELECT source, target, level FROM edges ORDER BY level, source")
    assert edges == [
        {"source": "https://b.test/post", "target": "https://s.test/", "level": 2},
        {"source": "https://b.test/post", "target": "https://d.test/", "level": 2},
        {"source": "https://c.test/", "target": "https://s.test/", "level": 2},
        {"source": "https://d.test/", "target": "https://b.test/post", "level": 3},
    ]


def test_verification_classifies_hyperlink_mention_and_unverified(tmp_path):
    db = tmp_path / "graph.sqlite3"
    mapping = {
        "https://s.test/": [
            "https://a.test/link",
            "https://b.test/text",
            "https://c.test/nothing",
        ],
    }
    pages = {
        "https://a.test/link": '<a href="https://s.test/">source</a>',
        "https://b.test/text": "As seen at https://s.test/ today.",
        "https://c.test/nothing": "no reference",
    }
    summary = run_crawl(db, mapping, pages=pages)

    assert summary["new_backlinks"] == 2
    relations = {
        row["url"]: row["relation"]
        for row in query(db, "SELECT url, relation FROM nodes WHERE kind='backlink'")
    }
    assert relations == {
        "https://a.test/link": "hyperlink",
        "https://b.test/text": "mention",
    }


def test_verification_can_keep_unverified_candidates(tmp_path):
    db = tmp_path / "graph.sqlite3"
    mapping = {"https://s.test/": ["https://c.test/nothing"]}
    pages = {"https://c.test/nothing": "no reference"}
    summary = run_crawl(db, mapping, pages=pages, keep_unverified=True)
    assert summary["new_backlinks"] == 1
    assert query(db, "SELECT relation FROM nodes WHERE kind='backlink'") == [
        {"relation": "unverified"}
    ]


def test_internal_engine_and_filtered_links_are_ignored(tmp_path):
    db = tmp_path / "graph.sqlite3"
    mapping = {
        "https://s.test/": [
            "https://s.test/internal",
            "https://www.s.test/internal",
            "https://www.bing.com/search?q=x",
            "https://b.test/keep",
            "https://b.test/drop",
        ],
    }
    run_crawl(
        db,
        mapping,
        verify=False,
        allow_pattern=r"keep|internal",
        deny_pattern=r"/drop",
    )
    urls = {row["url"] for row in query(db, "SELECT url FROM nodes")}
    assert urls == {"https://s.test/", "https://b.test/keep"}

    db2 = tmp_path / "graph2.sqlite3"
    run_crawl(db2, mapping, verify=False, include_internal=True)
    urls = {row["url"] for row in query(db2, "SELECT url FROM nodes")}
    assert "https://s.test/internal" in urls
    assert "https://www.bing.com/search?q=x" not in urls


def test_max_depth_stops_without_storing_the_next_level(tmp_path):
    db = tmp_path / "graph.sqlite3"
    mapping = {"https://s.test/": ["https://b.test/"]}
    summary = run_crawl(db, mapping, verify=False, max_depth=1)
    assert summary["stop_reason"] == "max-depth"
    urls = {row["url"] for row in query(db, "SELECT url FROM nodes")}
    assert urls == {"https://s.test/"}



def test_max_queries_leaves_targets_pending(tmp_path):
    db = tmp_path / "graph.sqlite3"
    mapping = {"https://s.test/": ["https://b.test/", "https://c.test/"]}
    summary = run_crawl(db, mapping, verify=False, max_queries=1)
    assert summary["stop_reason"] == "max-queries"
    assert summary["targets_queried"] == 1
    pending = query(db, "SELECT url FROM nodes WHERE status='pending'")
    assert {row["url"] for row in pending} == {
        "https://b.test/",
        "https://c.test/",
    }


def test_rerun_does_not_duplicate_rows_or_requery(tmp_path):
    db = tmp_path / "graph.sqlite3"
    mapping = {
        "https://s.test/": ["https://b.test/"],
        "https://b.test/": ["https://s.test/"],
    }
    run_crawl(db, mapping, verify=False)
    before = query(db, "SELECT url FROM nodes ORDER BY url")
    again = run_crawl(db, mapping, verify=False)
    assert again["targets_queried"] == 0
    assert again["stop_reason"] == "converged"
    assert query(db, "SELECT url FROM nodes ORDER BY url") == before


def test_provider_error_is_recorded_without_stopping_the_crawl(tmp_path):
    db = tmp_path / "graph.sqlite3"

    def broken(target, options):
        return backlinks.ProviderResult("broken", error="boom")

    summary = run_crawl(db, {}, registry={"broken": broken}, providers=["broken"])
    assert summary["stop_reason"] == "converged"
    assert query(db, "SELECT status, error FROM queries") == [
        {"status": "error", "error": "boom"}
    ]
    assert query(db, "SELECT status FROM nodes") == [{"status": "done"}]


def test_load_index_jsonl_and_csv(tmp_path):
    jsonl = tmp_path / "index.jsonl"
    jsonl.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "source": "https://b.test/post",
                        "target": "https://s.test/",
                        "relation": "hyperlink",
                    }
                ),
                json.dumps(
                    {"referrer": "https://c.test/", "target": "https://s.test/"}
                ),
            ]
        ),
        encoding="utf-8",
    )
    index = backlinks.load_index(jsonl)
    assert [link.url for link in index["https://s.test/"]] == [
        "https://b.test/post",
        "https://c.test/",
    ]

    csv_path = tmp_path / "index.csv"
    csv_path.write_text(
        "source,target,relation\nhttps://d.test/,https://s.test/,mention\n",
        encoding="utf-8",
    )
    index = backlinks.load_index(csv_path)
    assert index["https://s.test/"][0].relation == "mention"


def test_cli_runs_offline_with_a_local_index(tmp_path):
    db = tmp_path / "graph.sqlite3"
    index = tmp_path / "index.jsonl"
    index.write_text(
        "\n".join(
            [
                json.dumps(
                    {"source": "https://b.test/post", "target": "https://s.test/"}
                ),
                json.dumps(
                    {"source": "https://c.test/", "target": "https://b.test/post"}
                ),
                json.dumps(
                    {"source": "https://s.test/", "target": "https://c.test/"}
                ),
            ]
        ),
        encoding="utf-8",
    )
    code = backlinks.main(
        [
            "--seed",
            "https://s.test/",
            "--db",
            str(db),
            "--providers",
            "index",
            "--index-file",
            str(index),
            "--max-depth",
            "6",
            "--no-verify",
        ]
    )
    assert code == 0
    levels = {
        row["url"]: row["level"]
        for row in query(db, "SELECT url, level FROM nodes")
    }
    assert levels == {
        "https://s.test/": 1,
        "https://b.test/post": 2,
        "https://c.test/": 3,
    }
    assert query(db, "SELECT value FROM meta WHERE key='converged'") == [
        {"value": "yes"}
    ]


def test_export_nodes_and_edges_carry_labels(tmp_path):
    db = tmp_path / "graph.sqlite3"
    mapping = {
        "https://s.test/": ["https://b.test/post"],
        "https://b.test/post": ["https://s.test/"],
    }
    run_crawl(db, mapping, verify=False)

    nodes_path = tmp_path / "nodes.jsonl"
    assert backlinks.export_nodes(db, nodes_path) == 2
    records = [
        json.loads(line)
        for line in nodes_path.read_text(encoding="utf-8").splitlines()
    ]
    seed = next(row for row in records if row["url"] == "https://s.test/")
    link = next(row for row in records if row["url"] == "https://b.test/post")
    assert (seed["role"], seed["label"], seed["hop"]) == ("seed", "一级种子", 0)
    assert (link["role"], link["label"], link["hop"]) == (
        "backlink",
        "一级反向链接",
        1,
    )

    edges_path = tmp_path / "edges.jsonl"
    assert backlinks.export_edges(db, edges_path) == 2
    edges = [
        json.loads(line)
        for line in edges_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {edge["source"] for edge in edges} == {
        "https://b.test/post",
        "https://s.test/",
    }

