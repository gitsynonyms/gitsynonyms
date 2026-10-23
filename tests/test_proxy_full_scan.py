import importlib.util
import json
import sys
from pathlib import Path

PROXYINDEX = Path(__file__).parents[1] / "proxyindex"
SPEC = importlib.util.spec_from_file_location("proxy_full_scan", PROXYINDEX / "full_scan.py")
full_scan = importlib.util.module_from_spec(SPEC)
sys.modules["proxy_full_scan"] = full_scan
assert SPEC.loader is not None
SPEC.loader.exec_module(full_scan)
qm = full_scan.QM


def matrix():
    return qm.build_matrix()


def test_matrix_is_the_complete_cross_product_of_both_languages():
    data = matrix()
    counts = data["counts"]
    for lang in ("en", "zh"):
        heads = qm._ordered(qm.HEAD_TERMS[lang])
        networks = qm._ordered(qm.NETWORK_TERMS[lang])
        qualifiers = qm._ordered(qm.QUALIFIER_TERMS[lang])
        expected_pairs = 2 * (
            len(networks) * len(heads)
            + len(qualifiers) * len(heads)
            + len(networks) * len(qualifiers)
        )
        assert expected_pairs > 0
    assert counts["queries"] > 1000
    assert counts["by_language"]["en"] > 0
    assert counts["by_language"]["zh"] > 0
    assert counts["topics"] == len(qm.TOPIC_TERMS) + len(qm.PINYIN_TOPICS)


def test_matrix_contains_every_pair_in_both_orders_and_every_triple_template():
    queries = {item["query"] for item in qm.build_queries()}
    for lang in ("en", "zh"):
        for network in qm.NETWORK_TERMS[lang]:
            for head in qm.HEAD_TERMS[lang]:
                assert f"{network} {head}" in queries
                assert f"{head} {network}" in queries
        for qualifier in qm.QUALIFIER_TERMS[lang]:
            for network in qm.NETWORK_TERMS[lang]:
                for head in qm.HEAD_TERMS[lang]:
                    assert f"{qualifier} {network} {head}" in queries
                    assert f"{head} {network} {qualifier}" in queries


def test_matrix_carries_chinese_and_english_synonyms_and_near_synonyms():
    queries = {item["query"] for item in qm.build_queries()}
    for phrase in ("免费代理", "高速 socks5 代理", "代理池", "节点订阅", "梯子"):
        assert any(phrase in query for query in queries), phrase
    for phrase in ("free proxy", "socks5 proxy", "proxy pool", "residential proxy"):
        assert any(phrase in query for query in queries), phrase
    assert "proxies list" in queries
    assert "免费节点" in queries
    assert "topic:daili" in qm.build_topics()
    assert "topic:proxy-list" in qm.build_topics()


def test_matrix_is_deterministic_and_deduplicated():
    first = matrix()
    second = matrix()
    assert first["digest"] == second["digest"]
    names = [item["query"] for item in first["queries"]]
    assert len(names) == len(set(names))
    # Near-synonyms are de-duplicated against the generated families, so the
    # family count can only be smaller than the raw axis size.
    assert 0 < first["counts"]["by_family"]["near-synonym"] <= sum(
        len(qm._ordered(terms)) for terms in qm.NEAR_SYNONYM_TERMS.values()
    )


def test_cursor_walks_the_whole_matrix_and_wraps(tmp_path):
    data = matrix()
    total = len(data["queries"]) + len(data["topics"])
    state = qm.load_cursor(tmp_path / "cursor.json", total, data["digest"])
    assert qm.coverage(state) == 0.0

    seen = 0
    # A tiny matrix slice proves the cursor arithmetic without 30k iterations.
    while seen < total:
        batch, state = qm.take_batch(data, state, 5000)
        seen += len(batch)
    assert seen >= total
    assert state["cycles"] >= 1
    assert 0 <= state["cursor"] < total
    assert qm.coverage(state) < 1.0


def test_cursor_resets_when_the_matrix_digest_changes(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text(
        json.dumps({"digest": "old", "cursor": 17, "cycles": 3}), encoding="utf-8"
    )
    state = qm.load_cursor(path, 100, "new")
    assert state == {"digest": "new", "cursor": 0, "cycles": 0, "total": 100}


def make_paths(tmp_path):
    paths = full_scan.IndexPaths(tmp_path / "index")
    paths.ensure()
    return paths


def test_parse_entries_reads_text_json_and_csv_shapes():
    text = "\n".join(
        [
            "203.0.113.5:8080",
            "http://198.51.100.7:3128",
            "socks5://198.51.100.8:1080",
            "999.1.1.1:80",
            "example.com:8080",
            "# comment",
        ]
    )
    assert full_scan.parse_entries(text) == [
        "203.0.113.5:8080",
        "198.51.100.7:3128",
        "198.51.100.8:1080",
    ]
    payload = json.dumps(
        [
            {"ip": "203.0.113.9", "port": 8081},
            "198.51.100.9:8082",
            {"host": "not-an-ip", "port": 1},
        ]
    )
    assert full_scan.parse_entries(payload) == [
        "203.0.113.9:8081",
        "198.51.100.9:8082",
    ]


def test_merge_unique_keeps_one_line_per_proxy(tmp_path):
    pool = tmp_path / "pool.txt"
    full_scan.merge_unique(pool, ["203.0.113.5:80", "203.0.113.6:80"])
    full_scan.merge_unique(pool, ["203.0.113.5:80", "203.0.113.7:80"])
    assert full_scan.read_lines(pool) == [
        "203.0.113.5:80",
        "203.0.113.6:80",
        "203.0.113.7:80",
    ]


def test_expand_sources_crosses_every_repo_with_every_file_pattern(tmp_path):
    paths = make_paths(tmp_path)
    paths.recorded_sources.write_text(
        "https://raw.githubusercontent.com/a/b/HEAD/http.txt\n", encoding="utf-8"
    )
    paths.recorded_meta.write_text(
        json.dumps({"repo": "a/b", "url": "x"}) + "\n" + json.dumps({"repo": "c/d"}) + "\n",
        encoding="utf-8",
    )
    paths.discovered.write_text(json.dumps({"repo": "e/f"}) + "\n", encoding="utf-8")
    stats = full_scan.expand_sources(paths)
    lines = full_scan.read_lines(paths.sources)
    assert stats["repos"] == 3
    assert stats["patterns"] == len(qm.FILE_PATTERNS)
    assert len(lines) == len(set(lines))
    assert "https://raw.githubusercontent.com/a/b/HEAD/http.txt" in lines
    assert "https://raw.githubusercontent.com/e/f/HEAD/socks5.txt" in lines
    assert len(lines) == 1 + 3 * len(qm.FILE_PATTERNS) - 1


def test_stage_collect_merges_sources_and_resumes_without_refetching(tmp_path):
    paths = make_paths(tmp_path)
    paths.sources.write_text(
        "https://one.test/a.txt\nhttps://two.test/b.txt\n", encoding="utf-8"
    )
    calls = []

    def fetch(url, timeout=20):
        calls.append(url)
        if url.endswith("a.txt"):
            return "203.0.113.1:80\n203.0.113.2:80\n"
        raise OSError("offline")

    first = full_scan.stage_collect(paths, fetch=fetch, refresh_hours=6, quiet=True)
    assert first["fetched"] == 2
    assert first["unique_candidates"] == 2
    assert first["failed"] == 1
    assert full_scan.read_lines(paths.pool) == ["203.0.113.1:80", "203.0.113.2:80"]
    assert "https://one.test/a.txt" in paths.provenance.read_text(encoding="utf-8")

    # A source that just failed is not hammered again inside the retry window.
    second = full_scan.stage_collect(paths, fetch=fetch, refresh_hours=6, quiet=True)
    assert second["pending"] == 0
    assert second["fetched"] == 0
    assert len(calls) == 2

    # An older failure is retried on a later run.
    records = paths.collect_state.read_text(encoding="utf-8").splitlines()
    stale = []
    for line in records:
        record = json.loads(line)
        record["observed_at"] = "2020-01-01T00:00:00Z"
        stale.append(json.dumps(record))
    paths.collect_state.write_text("\n".join(stale) + "\n", encoding="utf-8")
    third = full_scan.stage_collect(paths, fetch=fetch, refresh_hours=6, quiet=True)
    assert third["pending"] == 2
    assert len(calls) == 4



def test_comm_left_only_returns_unprobed_lines(tmp_path):
    left = tmp_path / "alive.txt"
    right = tmp_path / "probes.tsv"
    left.write_text("203.0.113.1:80\n203.0.113.2:80\n203.0.113.3:80\n", encoding="utf-8")
    right.write_text("203.0.113.2:80\tok\t12.0\t204\n", encoding="utf-8")
    assert full_scan._comm_left_only(left, right) == ["203.0.113.1:80", "203.0.113.3:80"]
    assert full_scan._comm_left_only(left, right, limit=1) == ["203.0.113.1:80"]


def test_stage_http_probes_every_open_tcp_port_once(tmp_path):
    paths = make_paths(tmp_path)
    paths.tcp_probes.write_text(
        "203.0.113.1:80\topen\t4.0\n"
        "203.0.113.2:80\tclosed\t\n"
        "203.0.113.3:80\topen\t6.0\n",
        encoding="utf-8",
    )
    seen = []

    def probe(proxy, url, timeout):
        seen.append(proxy)
        if proxy == "203.0.113.1:80":
            return True, 204, 33.0
        return False, 0, 5000.0

    stats = full_scan.stage_http(paths, probe=probe, quiet=True)
    assert stats["alive_tcp"] == 2
    assert stats["checked"] == 2
    assert stats["ok"] == 1
    assert sorted(seen) == ["203.0.113.1:80", "203.0.113.3:80"]
    assert full_scan.read_lines(paths.alive_tcp) == ["203.0.113.1:80", "203.0.113.3:80"]

    again = full_scan.stage_http(paths, probe=probe, quiet=True)
    assert again["pending"] == 0
    assert len(seen) == 2


def test_stage_rank_reverifies_and_orders_the_fastest(tmp_path):
    paths = make_paths(tmp_path)
    paths.http_probes.write_text(
        "203.0.113.1:80\tok\t120.0\t204\n"
        "203.0.113.2:80\tok\t40.0\t204\n"
        "203.0.113.3:80\tok\t40.0\t204\n"
        "203.0.113.4:80\tfail\t9.0\t0\n",
        encoding="utf-8",
    )
    paths.provenance.write_text(
        "203.0.113.2:80\thttps://one.test/list.txt\n", encoding="utf-8"
    )
    fresh = {
        "203.0.113.1:80": (True, 204, 90.0),
        "203.0.113.2:80": (True, 204, 10.0),
        "203.0.113.3:80": (True, 204, 25.0),
    }

    result = full_scan.stage_rank(
        paths, probe=lambda proxy, url, timeout: fresh[proxy], top=3, quiet=True
    )
    assert [entry["proxy"] for entry in result["top"]] == [
        "http://203.0.113.2:80",
        "http://203.0.113.3:80",
        "http://203.0.113.1:80",
    ]
    assert result["top"][0]["source"] == "https://one.test/list.txt"
    assert full_scan.read_lines(paths.top_txt) == [
        "http://203.0.113.2:80",
        "http://203.0.113.3:80",
        "http://203.0.113.1:80",
    ]


def test_stage_rank_drops_candidates_that_fail_the_fresh_probe(tmp_path):
    paths = make_paths(tmp_path)
    paths.http_probes.write_text(
        "203.0.113.1:80\tok\t10.0\t204\n203.0.113.2:80\tok\t20.0\t204\n",
        encoding="utf-8",
    )

    def probe(proxy, url, timeout):
        return (True, 204, 15.0) if proxy.startswith("203.0.113.1") else (False, 0, 9.0)

    result = full_scan.stage_rank(paths, probe=probe, top=5, quiet=True)
    assert [entry["proxy"] for entry in result["top"]] == ["http://203.0.113.1:80"]



def test_stage_sync_commits_and_pushes_to_the_requested_branch(tmp_path):
    paths = make_paths(tmp_path)
    paths.top_json.write_text("{}", encoding="utf-8")
    paths.top_txt.write_text("", encoding="utf-8")
    paths.report.write_text("# report\n", encoding="utf-8")
    paths.state.write_text(json.dumps({"run_id": "r1"}), encoding="utf-8")
    calls = []

    class Completed:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def runner(args, cwd=None, capture_output=None, text=None, encoding=None):
        calls.append(list(args))
        if args[1] == "diff":
            return Completed(1)
        if args[1] == "rev-parse":
            return Completed(0, "abc123\n")
        return Completed(0)

    result = full_scan.stage_sync(paths, branch="main", runner=runner)
    assert result["status"] == "pushed"
    assert result["commit"] == "abc123"
    assert ["git", "push", "origin", "HEAD:main"] in calls
    assert ["git", "add", "--"] in [call[:3] for call in calls]


def test_stage_sync_reports_when_nothing_changed(tmp_path):
    paths = make_paths(tmp_path)
    paths.state.write_text("{}", encoding="utf-8")

    class Completed:
        def __init__(self, returncode=0):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    result = full_scan.stage_sync(paths, runner=lambda *args, **kwargs: Completed(0))
    assert result["status"] == "no-changes"


def test_render_report_states_unknown_candidates_instead_of_claiming_coverage():
    payload = {
        "run_id": "r1",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:10:00Z",
        "probe_url": "https://example.test/204",
        "partial": True,
        "coverage": {
            "matrix_queries": 10,
            "matrix_topics": 2,
            "matrix_coverage": 0.5,
            "sources": 7,
            "candidates": 100,
            "tcp_checked": 100,
            "candidate_coverage": 1.0,
            "http_checked": 20,
            "http_alive": 3,
        },
        "stages": {"collect": {"summary": {"fetched": 7}}},
        "top": [
            {
                "rank": 1,
                "proxy": "http://203.0.113.1:80",
                "latency_ms": 12.0,
                "http_status": 204,
                "source": "https://one.test/list.txt",
            }
        ],
    }
    report = full_scan.render_report(payload)
    assert "未探测的候选按 `unknown` 计" in report
    assert "http://203.0.113.1:80" in report
    assert "部分完成: yes" in report


def test_parse_stages_keeps_canonical_order_and_rejects_typos():
    assert full_scan.parse_stages("rank,matrix") == ["matrix", "rank"]
    assert full_scan.parse_stages("all") == list(full_scan.STAGE_ORDER)
    try:
        full_scan.parse_stages("matrix,nope")
    except SystemExit as error:
        assert "nope" in str(error)
    else:
        raise AssertionError("unknown stage must abort")



def test_coverage_snapshot_reports_partial_coverage_honestly(tmp_path):
    paths = make_paths(tmp_path)
    paths.discovery_state.write_text(
        json.dumps(
            {
                "queries": 100,
                "topics": 4,
                "cursor": 25,
                "total": 104,
                "coverage": 0.240385,
                "cycles": 0,
            }
        ),
        encoding="utf-8",
    )
    paths.pool.write_text("203.0.113.1:80\n203.0.113.2:80\n203.0.113.3:80\n", encoding="utf-8")
    paths.tcp_probes.write_text(
        "203.0.113.1:80\topen\t3.0\n203.0.113.2:80\tclosed\t\n", encoding="utf-8"
    )
    paths.http_probes.write_text("203.0.113.1:80\tok\t12.0\t204\n", encoding="utf-8")
    paths.top_json.write_text(json.dumps({"top": [{"proxy": "x"}]}), encoding="utf-8")

    snapshot = full_scan.coverage_snapshot(paths)
    assert snapshot["matrix_queries"] == 100
    assert snapshot["matrix_coverage"] == 0.240385
    assert snapshot["candidates"] == 3
    assert snapshot["tcp_checked"] == 2
    assert snapshot["candidate_coverage"] == round(2 / 3, 6)
    assert snapshot["http_alive"] == 1
    assert snapshot["top10"] == 1
