import importlib.util
from pathlib import Path
import urllib.error


SCRIPT = Path(__file__).parents[1] / "scripts" / "select_public_ytdlp_proxies.py"
SPEC = importlib.util.spec_from_file_location("select_public_ytdlp_proxies", SCRIPT)
proxy_selector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy_selector)


def test_normalize_candidate_rejects_invalid_addresses():
    assert proxy_selector.normalize_candidate("203.0.113.5:8080") == "http://203.0.113.5:8080"
    assert proxy_selector.normalize_candidate("https://198.51.100.10:8443/") == (
        "http://198.51.100.10:8443"
    )
    assert proxy_selector.normalize_candidate("256.1.1.1:80") is None
    assert proxy_selector.normalize_candidate("203.0.113.5:0") is None
    assert proxy_selector.normalize_candidate("proxy.example:8080") is None


def test_fetch_candidates_reads_multiple_lists_and_caps_each_source():
    class Response:
        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.content.encode()

    def open_url(url, timeout):
        assert timeout == proxy_selector.FETCH_TIMEOUT_SECONDS
        return Response("\n".join(f"192.0.2.{index}:8080" for index in range(1, 21)))

    candidates = proxy_selector.fetch_candidates(open_url)

    assert len(candidates) == 3
    assert all(len(source_candidates) == proxy_selector.MAX_CANDIDATES_PER_SOURCE for source_candidates in candidates.values())


def test_fetch_candidates_keeps_direct_fallback_when_public_sources_fail():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"192.0.2.1:8080\n"

    first_source = next(iter(proxy_selector.PROXY_LISTS.values()))

    def open_url(url, timeout):
        if url != first_source:
            raise urllib.error.URLError("source unavailable")
        return Response()

    candidates = proxy_selector.fetch_candidates(open_url)

    assert len(candidates) == 1
    assert len(next(iter(candidates.values()))) == 1


def test_select_proxies_returns_fastest_bounded_candidates():
    candidates = {
        "source-a": ["http://192.0.2.1:80", "http://192.0.2.2:80"],
        "source-b": ["http://192.0.2.2:80", "http://192.0.2.3:80"],
    }
    latencies = {
        "http://192.0.2.1:80": 0.4,
        "http://192.0.2.2:80": 0.1,
        "http://192.0.2.3:80": 0.2,
    }

    selected, source_counts = proxy_selector.select_proxies(candidates, latencies.get)

    assert selected == ["http://192.0.2.2:80", "http://192.0.2.3:80", "http://192.0.2.1:80"]
    assert source_counts == {"source-a": 2, "source-b": 2}


def test_select_proxies_returns_empty_when_none_pass_probe():
    selected, _source_counts = proxy_selector.select_proxies(
        {"source-a": ["http://192.0.2.1:80"], "source-b": ["http://192.0.2.2:80"]},
        lambda _proxy: None,
    )

    assert selected == []


def test_write_github_env_stores_json_as_one_value(tmp_path):
    env_file = tmp_path / "github_env"

    proxy_selector.write_github_env(
        "YTDLP_PROXY_POOL_JSON",
        '["http://192.0.2.1:8080","http://192.0.2.2:8080"]',
        str(env_file),
    )

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("YTDLP_PROXY_POOL_JSON<<GH_ENV_")
    assert lines[1] == '["http://192.0.2.1:8080","http://192.0.2.2:8080"]'
    assert lines[2] == lines[0].removeprefix("YTDLP_PROXY_POOL_JSON<<")
