import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "select_public_ytdlp_proxy.py"
SPEC = importlib.util.spec_from_file_location("select_public_ytdlp_proxy", SCRIPT)
proxy_selector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy_selector)


def test_normalize_candidate_accepts_only_ipv4_http_proxy_addresses():
    assert proxy_selector.normalize_candidate("203.0.113.5:8080") == "http://203.0.113.5:8080"
    assert proxy_selector.normalize_candidate("https://198.51.100.10:8443/") == (
        "http://198.51.100.10:8443"
    )
    assert proxy_selector.normalize_candidate("256.1.1.1:80") is None
    assert proxy_selector.normalize_candidate("203.0.113.5:0") is None
    assert proxy_selector.normalize_candidate("203.0.113.5:65536") is None
    assert proxy_selector.normalize_candidate("proxy.example:8080") is None
    assert proxy_selector.normalize_candidate("http://user:pass@203.0.113.5:80") is None


def test_select_proxy_chooses_fastest_candidate_across_sources():
    candidates = {
        "source-a": ["http://203.0.113.1:80", "http://203.0.113.2:80"],
        "source-b": ["http://203.0.113.2:80", "http://203.0.113.3:80"],
    }
    latencies = {
        "http://203.0.113.1:80": 0.4,
        "http://203.0.113.2:80": 0.1,
        "http://203.0.113.3:80": None,
    }

    proxy, latency, source_counts = proxy_selector.select_proxy(
        candidates, lambda candidate: latencies[candidate]
    )

    assert proxy == "http://203.0.113.2:80"
    assert latency == 0.1
    assert source_counts == {"source-a": 2, "source-b": 1}


def test_select_proxy_fails_when_no_candidate_passes_probe():
    try:
        proxy_selector.select_proxy(
            {"source-a": ["http://203.0.113.1:80"]},
            lambda _: None,
        )
    except RuntimeError as error:
        assert "No working public proxy" in str(error)
    else:
        raise AssertionError("Expected selection with no working proxies to fail")


def test_write_github_env_uses_multiline_safe_delimiter(tmp_path):
    env_file = tmp_path / "github_env"

    proxy_selector.write_github_env(
        "YTDLP_PROXY", "http://203.0.113.4:8080", str(env_file)
    )

    content = env_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert lines[0].startswith("YTDLP_PROXY<<GH_ENV_")
    assert lines[1] == "http://203.0.113.4:8080"
    assert lines[2] == lines[0].removeprefix("YTDLP_PROXY<<")


def test_write_github_env_rejects_line_breaks(tmp_path):
    env_file = tmp_path / "github_env"

    try:
        proxy_selector.write_github_env("YTDLP_PROXY", "http://proxy\nOTHER=x", str(env_file))
    except ValueError as error:
        assert "line breaks" in str(error)
    else:
        raise AssertionError("Expected line breaks in the proxy URL to be rejected")


def test_load_pool_reads_and_normalizes_valid_entries(tmp_path):
    pool = tmp_path / "alive_http.txt"
    pool.write_text("203.0.113.5:8080\nbad line\nhttps://198.51.100.10:8443/\n", encoding="utf-8")

    assert proxy_selector.load_pool(pool) == [
        "http://203.0.113.5:8080",
        "http://198.51.100.10:8443",
    ]


def test_load_pool_returns_empty_list_when_pool_is_missing(tmp_path):
    assert proxy_selector.load_pool(tmp_path / "missing.txt") == []


def test_select_fastest_working_returns_the_fastest_candidate():
    candidates = [
        "http://203.0.113.1:80",
        "http://203.0.113.2:80",
        "http://203.0.113.3:80",
    ]
    latencies = {
        "http://203.0.113.1:80": 0.3,
        "http://203.0.113.2:80": 0.05,
        "http://203.0.113.3:80": None,
    }

    assert proxy_selector.select_fastest_working(
        candidates, lambda candidate: latencies[candidate]
    ) == ("http://203.0.113.2:80", 0.05)


def test_select_fastest_working_returns_none_when_no_candidate_works():
    assert proxy_selector.select_fastest_working(["http://203.0.113.1:80"], lambda _: None) is None
