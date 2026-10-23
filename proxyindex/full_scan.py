#!/usr/bin/env python3
"""全量代理扫描流水线：list² 全量 → 测速 → top10 → 同步 main。

The proxy sub-system used to fetch three hand-picked lists and cap each at ten
candidates.  This module replaces that with one fixed, repeatable flow that the
material-acquisition code calls on every run:

    matrix     enumerate the full Chinese/English synonym matrix (list²)
    discover   walk the GitHub search matrix with a resumable cursor
    sources    expand every repository x conventional file path (list² again)
    collect    fetch every source, merge and de-duplicate the candidate pool
    probe      two-pass validation: TCP reachability, then a real HTTP request
    rank       keep the ten fastest verified proxies, re-probing them fresh
    sync       commit the result set and push it to the cloud repository's main

Every stage is resumable and every run records what it actually covered, so a
report never claims more than was observed: a proxy that was not probed is
`unknown`, not `ok`.

Typical use::

    python proxyindex/full_scan.py scan                 # full flow, no push
    python proxyindex/full_scan.py scan --sync          # ... then push to main
    python proxyindex/full_scan.py scan --stages matrix,discover
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import http.client
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

INDEX = Path(__file__).resolve().parent
ROOT = INDEX.parent
SCHEMA = "gitsynonyms.proxy.scan/1"
TOP_SCHEMA = "gitsynonyms.proxy.top10/1"

DEFAULT_PROBE_URL = "https://www.youtube.com/generate_204"
DEFAULT_CANARY = "http://example.com/"
MAX_SOURCE_BYTES = 4_000_000
DEFAULT_WORKERS = 64
DEFAULT_PROBE_CONCURRENCY = 128
DEFAULT_TIMEOUT = 8.0
DEFAULT_BUDGET_SECONDS = 1500.0
DEFAULT_QUERY_BUDGET = 600
DEFAULT_PROBE_BUDGET = 0  # 0 = no per-run cap, the time budget is the limit
MAX_TCP_PER_RUN = 200_000  # keeps the per-run candidate list inside the memory budget
DEFAULT_TOP = 10
GH_SEARCH_INTERVAL = 2.2  # 30 authenticated search calls per minute

IPV4_PORT = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}$")
IPV4_PORT_ANY = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3}):(\d{2,5})(?![\d])")
SCHEME = re.compile(r"^(?:https?|socks[45]h?|socks)://", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None = None) -> str:
    moment = moment or utc_now()
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def run_id() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# Paths and small durable-file helpers
# ---------------------------------------------------------------------------


class IndexPaths:
    """Every artefact of the flow, in one place so stages stay independent."""

    def __init__(self, root: Path | str = INDEX) -> None:
        self.root = Path(root)
        self.matrix = self.root / "query-matrix.json"
        self.queries = self.root / "queries.txt"
        self.discovery_state = self.root / "discovery-state.json"
        self.discovered = self.root / "discovered.jsonl"
        self.recorded_sources = self.root / "sources.txt"
        self.recorded_meta = self.root / "sources.jsonl"
        self.sources = self.root / "sources.full.txt"
        self.collect_state = self.root / "collect.jsonl"
        self.pool = self.root / "pool.txt"
        self.provenance = self.root / "pool.provenance.tsv"
        self.tcp_probes = self.root / "tcp-probes.tsv"
        self.http_probes = self.root / "http-probes.tsv"
        self.alive_tcp = self.root / "alive_tcp.txt"
        self.alive_http = self.root / "alive_http.txt"
        self.top_json = self.root / "top10.json"
        self.top_txt = self.root / "top10.txt"
        self.report = self.root / "scan-report.md"
        self.state = self.root / "scan-state.json"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


def read_lines(path: Path) -> list[str]:
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return []


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def merge_unique(path: Path, additions: list[str]) -> int:
    """Append ``additions`` to ``path`` keeping one line per unique value.

    Uses the system ``sort`` so a multi-million line pool never has to fit in
    the Python heap; only the new lines are materialised in memory.
    """
    if not additions:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as extra:
        extra.write("\n".join(additions) + "\n")
        extra_path = Path(extra.name)
    merged = path.with_suffix(path.suffix + ".merged")
    try:
        with open(merged, "wb") as output:
            with open(path, "rb") as existing:
                shutil.copyfileobj(existing, output)
            with open(extra_path, "rb") as fresh:
                shutil.copyfileobj(fresh, output)
        subprocess.run(
            ["sort", "-u", "-o", str(merged), str(merged)], check=True
        )
        merged.replace(path)
    except (OSError, subprocess.SubprocessError):
        with open(path, "a", encoding="utf-8") as sink:
            for line in additions:
                sink.write(line + "\n")
    finally:
        extra_path.unlink(missing_ok=True)
    return len(additions)


def merge_file_unique(target: Path, addition: Path) -> None:
    """Stream ``target`` + ``addition`` through ``sort -u`` without loading them."""
    if not addition.exists() or addition.stat().st_size == 0:
        addition.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    merged = target.with_name(target.name + ".merged")
    command = (
        f"LC_ALL=C sort -u {shlex(target)} {shlex(addition)} -o {shlex(merged)}"
        if target.exists()
        else f"LC_ALL=C sort -u {shlex(addition)} -o {shlex(merged)}"
    )
    completed = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[:300] or "sort -u failed")
    merged.replace(target)
    addition.unlink(missing_ok=True)



def normalize_proxy(value: str) -> str | None:
    """Return ``ip:port`` for a supported entry, or ``None``."""
    entry = value.strip()
    entry = SCHEME.sub("", entry)
    entry = entry.rstrip("/")
    if not IPV4_PORT.match(entry):
        return None
    address, _, port_text = entry.partition(":")
    try:
        ipaddress.IPv4Address(address)
        port = int(port_text)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return f"{address}:{port}"


def parse_entries(text: str) -> list[str]:
    """Extract every proxy from a list file: line, JSON or CSV shapes."""
    entries: list[str] = []
    stripped = text.lstrip()
    if stripped[:1] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    host = item.get("ip") or item.get("host") or item.get("proxy")
                    port = item.get("port")
                    if host and port:
                        proxy = normalize_proxy(f"{host}:{port}")
                        if proxy:
                            entries.append(proxy)
                elif isinstance(item, str):
                    proxy = normalize_proxy(item)
                    if proxy:
                        entries.append(proxy)
    for match in IPV4_PORT_ANY.finditer(text):
        proxy = normalize_proxy(match.group(0))
        if proxy:
            entries.append(proxy)
    return list(dict.fromkeys(entries))


def proxy_url(entry: str) -> str:
    return entry if "://" in entry else f"http://{entry}"



def _load_query_matrix():
    try:  # normal script execution: proxyindex/ is sys.path[0]
        import query_matrix as module  # type: ignore[import-not-found]

        return module
    except ImportError:  # imported from a test or another directory
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "proxy_query_matrix", INDEX / "query_matrix.py"
        )
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


QM = _load_query_matrix()


# ---------------------------------------------------------------------------
# Stage: matrix + discovery
# ---------------------------------------------------------------------------


def _run_gh(args: list[str]) -> str:
    completed = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8"
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[:300] or "gh failed")
    return completed.stdout


def search_repositories(
    query: str,
    *,
    pages: int = 2,
    runner=None,
    sleep=time.sleep,
    interval: float = GH_SEARCH_INTERVAL,
) -> list[dict]:
    """Run one GitHub repository search and return ``[{repo, stars, ...}]``."""
    runner = runner or _run_gh
    repos: list[dict] = []
    for page in range(1, max(1, pages) + 1):
        raw = runner(
            [
                "api",
                "-X",
                "GET",
                "search/repositories",
                "-f",
                f"q={query}",
                "-f",
                "per_page=100",
                "-f",
                f"page={page}",
                "-f",
                "sort=updated",
                "-f",
                "order=desc",
            ]
        )
        payload = json.loads(raw or "{}")
        items = payload.get("items") if isinstance(payload, dict) else None
        if not items:
            break
        for item in items:
            if not isinstance(item, dict) or not item.get("full_name"):
                continue
            repos.append(
                {
                    "repo": item["full_name"],
                    "stars": item.get("stargazers_count") or 0,
                    "pushed_at": item.get("pushed_at") or "",
                    "default_branch": item.get("default_branch") or "HEAD",
                }
            )
        if len(items) < 100:
            break
        sleep(interval)
    return repos



def stage_matrix(
    paths: IndexPaths,
    *,
    query_budget: int = DEFAULT_QUERY_BUDGET,
    pages: int = 2,
    offline: bool = False,
    runner=None,
    sleep=time.sleep,
    quiet: bool = False,
) -> dict:
    """Write the full matrix and walk it for ``query_budget`` queries."""
    matrix = QM.write_matrix(paths.matrix)
    write_lines(paths.queries, [item["query"] for item in matrix["queries"]])
    total = len(matrix["queries"]) + len(matrix["topics"])
    state = QM.load_cursor(paths.discovery_state, total, matrix["digest"])
    batch: list[dict] = []
    if not offline and query_budget > 0:
        batch, state = QM.take_batch(matrix, state, query_budget)
        with paths.discovered.open("a", encoding="utf-8") as sink:
            for index, item in enumerate(batch):
                if index:
                    sleep(GH_SEARCH_INTERVAL)
                try:
                    repos = search_repositories(
                        item["query"], pages=pages, runner=runner, sleep=sleep
                    )
                except (RuntimeError, ValueError, OSError) as error:
                    if not quiet:
                        print(
                            f"  discovery failed {item['query']!r}: {error}",
                            file=sys.stderr,
                        )
                    continue
                observed = iso()
                for repo in repos:
                    sink.write(
                        json.dumps(
                            {
                                **repo,
                                "query": item["query"],
                                "kind": item["kind"],
                                "observed_at": observed,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
    state["searched"] = len(batch)
    state["coverage"] = QM.coverage(state)
    state["queries"] = len(matrix["queries"])
    state["topics"] = len(matrix["topics"])
    state["digest"] = matrix["digest"]
    state["observed_at"] = iso()
    write_json(paths.discovery_state, state)
    if not quiet:
        print(
            f"matrix: {matrix['counts']['queries']} queries + "
            f"{matrix['counts']['topics']} topics, digest {matrix['digest'][:12]}, "
            f"searched {state['searched']}, cursor coverage {state['coverage']:.2%}"
        )
    return state


def known_repos(paths: IndexPaths) -> list[str]:
    repos: set[str] = set()
    for line in read_lines(paths.recorded_meta) + read_lines(paths.discovered):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        repo = str(record.get("repo") or "").strip()
        if repo:
            repos.add(repo)
    return sorted(repos)


def expand_sources(paths: IndexPaths) -> dict:
    """list² source expansion: recorded file URLs + repo x conventional paths."""
    urls: set[str] = set()
    for line in read_lines(paths.recorded_sources):
        if line.startswith(("http://", "https://")):
            urls.add(line)
    recorded = len(urls)
    repos = known_repos(paths)
    for repo in repos:
        for pattern in QM.FILE_PATTERNS:
            urls.add(f"https://raw.githubusercontent.com/{repo}/HEAD/{pattern}")
    write_lines(paths.sources, sorted(urls))
    return {
        "sources": len(urls),
        "recorded": recorded,
        "expanded": len(urls) - recorded,
        "repos": len(repos),
        "patterns": len(QM.FILE_PATTERNS),
    }



# ---------------------------------------------------------------------------
# Stage: collect
# ---------------------------------------------------------------------------


class Deadline:
    """A monotonic time budget shared by the long stages."""

    def __init__(self, seconds: float = DEFAULT_BUDGET_SECONDS) -> None:
        self.seconds = float(seconds)
        self.started = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def expired(self) -> bool:
        return self.seconds > 0 and self.elapsed() >= self.seconds

    def remaining(self) -> float:
        if self.seconds <= 0:
            return float("inf")
        return max(0.0, self.seconds - self.elapsed())


def fetch_text(url: str, *, timeout: float = 20.0, user_agent: str = "proxyindex/2.0"):
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_SOURCE_BYTES)
    return payload.decode("utf-8", "replace")


def last_collections(paths: IndexPaths) -> dict[str, dict]:
    state: dict[str, dict] = {}
    for line in read_lines(paths.collect_state):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = str(record.get("source") or "")
        if url:
            state[url] = record
    return state



def stage_collect(
    paths: IndexPaths,
    *,
    workers: int = DEFAULT_WORKERS,
    timeout: float = 20.0,
    refresh_hours: float = 6.0,
    deadline: Deadline | None = None,
    fetch=fetch_text,
    quiet: bool = False,
) -> dict:
    """Fetch every source whose last observation is older than ``refresh_hours``.

    New candidates are appended to ``pool.parts`` and merged with the system
    ``sort`` once at the end, so a multi-million line pool never has to be
    re-sorted (or held in memory) after every batch.
    """
    deadline = deadline or Deadline(0)
    sources = read_lines(paths.sources)
    history = last_collections(paths)
    cutoff = utc_now().timestamp() - refresh_hours * 3600
    pending: list[str] = []
    for url in sources:
        record = history.get(url)
        if record:
            try:
                observed = datetime.fromisoformat(
                    str(record.get("observed_at", "")).replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                observed = 0.0
            status = str(record.get("status") or "")
            window = cutoff if status in {"ok", "empty"} else cutoff - 18 * 3600
            if status == "missing":
                continue
            if observed >= window:
                continue
        pending.append(url)
    if deadline.expired():
        return {
            "sources": len(sources),
            "pending": len(pending),
            "fetched": 0,
            "failed": 0,
            "empty": 0,
            "entries": 0,
            "unique_candidates": 0,
            "partial": True,
            "note": "time budget exhausted before collection",
        }

    counters = {"fetched": 0, "failed": 0, "empty": 0, "entries": 0}
    parts = paths.root / "pool.parts"
    provenance_parts = paths.root / "provenance.parts"
    buffer_entries: list[str] = []
    buffer_provenance: list[str] = []
    buffer_records: list[str] = []

    def flush() -> None:
        nonlocal buffer_entries, buffer_provenance, buffer_records
        if not buffer_records:
            return
        with paths.collect_state.open("a", encoding="utf-8") as sink:
            sink.write("\n".join(buffer_records) + "\n")
        if buffer_entries:
            with parts.open("a", encoding="utf-8") as sink:
                sink.write("\n".join(buffer_entries) + "\n")
            with provenance_parts.open("a", encoding="utf-8") as sink:
                sink.write("\n".join(buffer_provenance) + "\n")
        buffer_entries, buffer_provenance, buffer_records = [], [], []

    def work(url: str) -> tuple[str, str, list[str], str]:
        try:
            text = fetch(url, timeout=timeout)
        except urllib.error.HTTPError as error:
            return url, "missing" if error.code == 404 else "error", [], f"HTTP {error.code}"
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            return url, "error", [], str(getattr(error, "reason", error))[:200]
        entries = parse_entries(text)
        return url, "ok" if entries else "empty", entries, ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures: dict[concurrent.futures.Future, str] = {}
        iterator = iter(pending)
        exhausted = False
        while not exhausted or futures:
            while not exhausted and len(futures) < max(1, workers) * 4:
                if deadline.expired():
                    exhausted = True
                    break
                try:
                    url = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                futures[executor.submit(work, url)] = url
            if not futures:
                break
            done, _ = concurrent.futures.wait(
                futures, timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                url = futures.pop(future)
                try:
                    source, status, entries, error = future.result()
                except Exception as exc:  # noqa: BLE001 - one source must not stop the scan
                    source, status, entries, error = url, "error", [], str(exc)[:200]
                counters["fetched"] += 1
                if status == "empty":
                    counters["empty"] += 1
                elif status != "ok":
                    counters["failed"] += 1
                counters["entries"] += len(entries)
                buffer_records.append(
                    json.dumps(
                        {
                            "source": source,
                            "status": status,
                            "entries": len(entries),
                            "error": error,
                            "observed_at": iso(),
                        },
                        ensure_ascii=False,
                    )
                )
                for entry in entries:
                    buffer_entries.append(entry)
                    buffer_provenance.append(f"{entry}\t{source}")
                if len(buffer_records) >= 50:
                    flush()
            if not quiet and counters["fetched"] % 2000 < max(1, len(done)):
                print(
                    f"  collect {counters['fetched']}/{len(pending)} sources, "
                    f"{counters['entries']} entries",
                    flush=True,
                )
    flush()
    before = count_lines(paths.pool)
    merge_file_unique(paths.pool, parts)
    merge_file_unique(paths.provenance, provenance_parts)
    return {
        "sources": len(sources),
        "pending": len(pending),
        **counters,
        "unique_candidates": count_lines(paths.pool) - before,
        "partial": deadline.expired() or counters["fetched"] < len(pending),
    }


# ---------------------------------------------------------------------------
# Stage: probe (TCP reachability, then a real HTTP request)
# ---------------------------------------------------------------------------


def _comm_left_only(left: Path, right: Path, *, limit: int = 0) -> list[str]:
    """Lines of ``left`` that are not in column 1 of ``right`` (external sort)."""
    if not left.exists():
        return []
    if not right.exists():
        return read_lines(left)[: limit or None]
    command = (
        "comm -23 "
        f"<(LC_ALL=C sort -u {shlex(left)}) "
        f"<(LC_ALL=C cut -f1 {shlex(right)} | LC_ALL=C sort -u)"
    )
    if limit:
        command += f" | head -n {int(limit)}"
    completed = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, encoding="utf-8"
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[:300] or "comm failed")
    return [line for line in completed.stdout.splitlines() if line]


def shlex(path: Path) -> str:
    import shlex as _shlex

    return _shlex.quote(str(path))


async def _tcp_probe(host: str, port: int, timeout: float, semaphore) -> float | None:
    async with semaphore:
        started = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout
            )
        except (OSError, asyncio.TimeoutError):
            return None
        elapsed = (time.monotonic() - started) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):
            pass
        return elapsed


async def _tcp_batch(entries: list[str], concurrency: int, timeout: float):
    semaphore = asyncio.Semaphore(max(1, concurrency))
    tasks = []
    for entry in entries:
        host, _, port_text = entry.rpartition(":")
        if not host or not port_text.isdigit():
            continue
        tasks.append(_tcp_probe(host, int(port_text), timeout, semaphore))
    for entry, result in zip(entries, await asyncio.gather(*tasks)):
        yield entry, result


def stage_tcp(
    paths: IndexPaths,
    *,
    concurrency: int = 512,
    timeout: float = 3.0,
    chunk: int = 20000,
    deadline: Deadline | None = None,
    batch_limit: int = 200000,
    quiet: bool = False,
) -> dict:
    """Give every pooled candidate a TCP verdict (open / closed / inconclusive)."""
    deadline = deadline or Deadline(0)
    pending = _comm_left_only(
        paths.pool, paths.tcp_probes, limit=batch_limit or MAX_TCP_PER_RUN
    )
    checked = opened = 0
    for start in range(0, len(pending), max(1, chunk)):
        if deadline.expired():
            break
        block = pending[start : start + chunk]
        with paths.tcp_probes.open("a", encoding="utf-8") as sink:
            for entry, latency in asyncio.run(
                _collect_tcp(block, concurrency, timeout)
            ):
                checked += 1
                if latency is None:
                    sink.write(f"{entry}\tclosed\t\n")
                else:
                    opened += 1
                    sink.write(f"{entry}\topen\t{latency:.1f}\n")
        if not quiet:
            print(
                f"  tcp {checked}/{len(pending)} checked, {opened} open",
                flush=True,
            )
    return {
        "pending": len(pending),
        "checked": checked,
        "open": opened,
        "partial": deadline.expired() or checked < len(pending),
    }


async def _collect_tcp(entries: list[str], concurrency: int, timeout: float):
    results = []
    async for item in _tcp_batch(entries, concurrency, timeout):
        results.append(item)
    return results



def _tcp_open(paths: IndexPaths) -> list[str]:
    """Proxies whose TCP port opened, from the append-only probe ledger."""
    source = paths.tcp_probes
    if not source.exists():
        return []
    completed = subprocess.run(
        [
            "bash",
            "-c",
            f"LC_ALL=C awk -F'\\t' '$2 == \"open\" {{print $1}}' {shlex(source)} "
            "| LC_ALL=C sort -u",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line]


def probe_http(
    proxy: str,
    probe_url: str = DEFAULT_PROBE_URL,
    timeout: float = 5.0,
) -> tuple[bool, int, float]:
    """Send one real request through ``proxy``; return (ok, status, latency_ms)."""
    url = proxy_url(proxy)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": url, "https": url})
    )
    request = urllib.request.Request(
        probe_url, headers={"User-Agent": "gitsynonyms-proxy-full-scan/1.0"}
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 0)
            response.read(1024)
    except (OSError, urllib.error.URLError, http.client.HTTPException, TimeoutError):
        return False, 0, (time.monotonic() - started) * 1000
    return 200 <= status < 400, status, (time.monotonic() - started) * 1000


def stage_http(
    paths: IndexPaths,
    *,
    probe_url: str = DEFAULT_PROBE_URL,
    concurrency: int = DEFAULT_PROBE_CONCURRENCY,
    timeout: float = 5.0,
    batch_limit: int = 50000,
    deadline: Deadline | None = None,
    probe=probe_http,
    quiet: bool = False,
) -> dict:
    """Probe every TCP-open proxy with a real HTTP request through it."""
    deadline = deadline or Deadline(0)
    alive = _tcp_open(paths)
    write_lines(paths.alive_tcp, alive)
    pending = _comm_left_only(paths.alive_tcp, paths.http_probes, limit=batch_limit)
    ok_count = 0
    errors = 0

    def work(proxy: str) -> tuple[str, bool, int, float, str]:
        try:
            ok, status, latency = probe(proxy, probe_url, timeout)
            return proxy, ok, status, latency, ""
        except Exception as exc:  # noqa: BLE001 - record, never abort the sweep
            return proxy, False, 0, 0.0, str(exc)[:160]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures: dict[concurrent.futures.Future, str] = {}
        iterator = iter(pending)
        exhausted = False
        checked = 0
        with paths.http_probes.open("a", encoding="utf-8") as sink:
            while not exhausted or futures:
                while not exhausted and len(futures) < max(1, concurrency) * 3:
                    if deadline.expired():
                        exhausted = True
                        break
                    try:
                        proxy = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    futures[pool.submit(work, proxy)] = proxy
                if not futures:
                    break
                done, _ = concurrent.futures.wait(
                    futures, timeout=1.0, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    proxy = futures.pop(future)
                    try:
                        proxy, ok, status, latency, error = future.result()
                    except Exception as exc:  # noqa: BLE001
                        ok, status, latency, error = False, 0, 0.0, str(exc)[:160]
                    checked += 1
                    errors += bool(error)
                    if ok:
                        ok_count += 1
                        sink.write(f"{proxy}\tok\t{latency:.1f}\t{status}\n")
                    else:
                        sink.write(f"{proxy}\tfail\t{latency:.1f}\t{status}\t{error}\n")
                sink.flush()
                if not quiet and checked and checked % 1000 == 0:
                    print(
                        f"  http {checked}/{len(pending)} probed, {ok_count} alive",
                        flush=True,
                    )
    return {
        "alive_tcp": len(alive),
        "pending": len(pending),
        "checked": checked,
        "ok": ok_count,
        "errors": errors,
        "partial": deadline.expired() or checked < len(pending),
    }



# ---------------------------------------------------------------------------
# Stage: rank
# ---------------------------------------------------------------------------


def load_http_results(paths: IndexPaths) -> dict[str, dict]:
    """Best successful observation per proxy from the append-only ledger."""
    results: dict[str, dict] = {}
    if not paths.http_probes.exists():
        return results
    with paths.http_probes.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or parts[1] != "ok":
                continue
            proxy = parts[0]
            try:
                latency = float(parts[2])
            except ValueError:
                continue
            status = parts[3] if len(parts) > 3 else ""
            current = results.get(proxy)
            if current is None or latency < current["latency_ms"]:
                results[proxy] = {"latency_ms": latency, "status": status}
    return results



def provenance_for(paths: IndexPaths, proxies: list[str]) -> dict[str, str]:
    """First source that contributed each proxy, read streaming."""
    wanted = set(proxies)
    found: dict[str, str] = {}
    if not wanted or not paths.provenance.exists():
        return found
    with paths.provenance.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            proxy, _, source = line.partition("\t")
            if proxy in wanted and proxy not in found:
                found[proxy] = source.strip()
                if len(found) == len(wanted):
                    break
    return found


def stage_rank(
    paths: IndexPaths,
    *,
    probe_url: str = DEFAULT_PROBE_URL,
    top: int = DEFAULT_TOP,
    verify: int = 60,
    timeout: float = 6.0,
    probe=probe_http,
    quiet: bool = False,
) -> dict:
    """Re-probe the fastest candidates fresh and publish the top ten."""
    results = load_http_results(paths)
    ordered = sorted(results.items(), key=lambda item: (item[1]["latency_ms"], item[0]))
    candidates = [proxy for proxy, _ in ordered[: max(top, verify)]]
    verified: list[tuple[float, str, int]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, verify))) as pool:
        futures = {pool.submit(probe, proxy, probe_url, timeout): proxy for proxy in candidates}
        for future in concurrent.futures.as_completed(futures):
            proxy = futures[future]
            try:
                ok, status, latency = future.result()
            except Exception:  # noqa: BLE001 - a failed re-probe just drops the proxy
                continue
            if ok:
                verified.append((latency, proxy, status))
    verified.sort()
    top_entries = []
    for rank, (latency, proxy, status) in enumerate(verified[:top], 1):
        top_entries.append(
            {
                "rank": rank,
                "proxy": proxy_url(proxy),
                "host": proxy,
                "latency_ms": round(latency, 1),
                "http_status": status,
                "verified_at": iso(),
            }
        )
    sources = provenance_for(paths, [entry["host"] for entry in top_entries])
    for entry in top_entries:
        entry["source"] = sources.get(entry["host"], "")
    write_lines(paths.top_txt, [entry["proxy"] for entry in top_entries])
    if not quiet:
        for entry in top_entries:
            print(f"  #{entry['rank']} {entry['proxy']} {entry['latency_ms']} ms")
    return {
        "pool": count_lines(paths.pool),
        "probed_ok": len(results),
        "candidates": len(candidates),
        "verified": len(verified),
        "top": top_entries,
    }




# ---------------------------------------------------------------------------
# Stage: sync to the cloud repository
# ---------------------------------------------------------------------------


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _repo_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _git(args: list[str], runner=subprocess.run) -> subprocess.CompletedProcess:
    return runner(
        ["git", *args], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8"
    )


def stage_sync(
    paths: IndexPaths,
    *,
    branch: str = "main",
    remote: str = "origin",
    message: str = "",
    files: list[str] | None = None,
    runner=subprocess.run,
) -> dict:
    """Commit the published artefacts and push them to ``remote/branch``."""
    relative = files or [_repo_path(paths.top_json), _repo_path(paths.top_txt), _repo_path(paths.report), _repo_path(paths.state)]
    message = message or (
        "Update proxy top10 from the full list² scan\n\n"
        f"run {read_json(paths.state, {}).get('run_id', 'unknown')}"
    )
    added = _git(["add", "--", *relative], runner=runner)
    if added.returncode != 0:
        return {"status": "error", "stage": "add", "detail": added.stderr.strip()[:300]}
    staged = _git(["diff", "--cached", "--quiet", "--", *relative], runner=runner)
    if staged.returncode == 0:
        return {"status": "no-changes", "files": relative}
    committed = _git(["commit", "-m", message], runner=runner)
    if committed.returncode != 0:
        return {
            "status": "error",
            "stage": "commit",
            "detail": (committed.stderr or committed.stdout).strip()[:300],
        }
    sha = _git(["rev-parse", "--short", "HEAD"], runner=runner).stdout.strip()
    fetched = _git(["fetch", remote, branch], runner=runner)
    if fetched.returncode != 0:
        return {
            "status": "error",
            "stage": "fetch",
            "detail": fetched.stderr.strip()[:300],
            "commit": sha,
        }
    pushed = _git(["push", remote, f"HEAD:{branch}"], runner=runner)
    if pushed.returncode != 0:
        rebased = _git(["rebase", f"{remote}/{branch}"], runner=runner)
        if rebased.returncode != 0:
            _git(["rebase", "--abort"], runner=runner)
            return {
                "status": "error",
                "stage": "push",
                "detail": (pushed.stderr or "").strip()[:300],
                "commit": sha,
            }
        pushed = _git(["push", remote, f"HEAD:{branch}"], runner=runner)
        if pushed.returncode != 0:
            return {
                "status": "error",
                "stage": "push",
                "detail": (pushed.stderr or "").strip()[:300],
                "commit": sha,
            }
    return {"status": "pushed", "commit": sha, "branch": branch, "files": relative}



# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


STAGE_ORDER = ("matrix", "sources", "collect", "probe", "rank", "sync")


def parse_stages(value: str) -> list[str]:
    if not value or value == "all":
        return list(STAGE_ORDER)
    requested = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(STAGE_ORDER))
    if unknown:
        raise SystemExit(f"unknown stage(s): {', '.join(unknown)}")
    return [stage for stage in STAGE_ORDER if stage in requested]


def coverage_snapshot(paths: IndexPaths) -> dict:
    discovery = read_json(paths.discovery_state, {})
    top = read_json(paths.top_json, {})
    pool = count_lines(paths.pool)
    tcp = count_lines(paths.tcp_probes)
    http_attempts = count_lines(paths.http_probes)
    results = load_http_results(paths)
    return {
        "matrix_queries": int(discovery.get("queries") or 0),
        "matrix_topics": int(discovery.get("topics") or 0),
        "discovery_cursor": int(discovery.get("cursor") or 0),
        "discovery_total": int(discovery.get("total") or 0),
        "matrix_coverage": float(discovery.get("coverage") or 0.0),
        "discovery_cycles": int(discovery.get("cycles") or 0),
        "sources": count_lines(paths.sources),
        "candidates": pool,
        "tcp_checked": tcp,
        "http_checked": http_attempts,
        "http_alive": len(results),
        "top10": len(top.get("top") or []),
        "candidate_coverage": round(min(1.0, tcp / pool), 6) if pool else 0.0,
        "alive_coverage": round(len(results) / tcp, 6) if tcp else 0.0,
    }


def render_report(payload: dict) -> str:
    coverage = payload["coverage"]
    lines = [
        "# 代理全量扫描报告 (proxy full scan)",
        "",
        f"- run: `{payload['run_id']}`",
        f"- 开始: {payload['started_at']}",
        f"- 结束: {payload['finished_at']}",
        f"- 探测目标: {payload['probe_url']}",
        f"- 部分完成: {'yes' if payload['partial'] else 'no'}",
        "",
        "## 覆盖 (coverage)",
        "",
        "| 项目 | 数值 |",
        "| --- | --- |",
        f"| 检索矩阵查询 | {coverage['matrix_queries']} |",
        f"| 检索矩阵主题 | {coverage['matrix_topics']} |",
        f"| 矩阵游标覆盖 | {coverage['matrix_coverage']:.2%} |",
        f"| 已发现来源 (list²) | {coverage['sources']} |",
        f"| 候选代理 | {coverage['candidates']} |",
        f"| TCP 已探测 | {coverage['tcp_checked']} ({coverage['candidate_coverage']:.2%}) |",
        f"| HTTP 已探测 | {coverage['http_checked']} |",
        f"| HTTP 可用 | {coverage['http_alive']} |",
        "",
        "未探测的候选按 `unknown` 计，不按 `ok` 计。",
        "",
        "## Top 10 (按实测时延)",
        "",
        "| # | proxy | 时延 (ms) | HTTP | 首次来源 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in payload.get("top") or []:
        lines.append(
            f"| {entry['rank']} | `{entry['proxy']}` | {entry['latency_ms']} | "
            f"{entry['http_status']} | `{entry.get('source', '')}` |"
        )
    if not payload.get("top"):
        lines.append("| - | 本轮未产生通过复验的代理 | - | - | - |")
    lines.extend(["", "## 阶段 (stages)", "", "| 阶段 | 结果 |", "| --- | --- |"])
    for stage, result in payload["stages"].items():
        summary = result.get("summary") if isinstance(result, dict) else result
        lines.append(f"| {stage} | `{json.dumps(summary, ensure_ascii=False)}` |")
    lines.append("")
    return "\n".join(lines)



def scan(args) -> dict:
    """Run the selected stages in order and publish ``top10.json``."""
    paths = IndexPaths(args.index_dir)
    paths.ensure()
    stages = parse_stages(args.stages)
    deadline = Deadline(args.budget_seconds)
    started = utc_now()
    identifier = run_id()
    payload = {
        "schema": SCHEMA,
        "run_id": identifier,
        "started_at": iso(started),
        "finished_at": "",
        "probe_url": args.probe_url,
        "partial": False,
        "settings": {
            "stages": stages,
            "workers": args.workers,
            "probe_concurrency": args.probe_concurrency,
            "timeout": args.timeout,
            "budget_seconds": args.budget_seconds,
            "query_budget": args.query_budget,
            "probe_budget": args.probe_budget,
            "refresh_hours": args.refresh_hours,
            "github": not args.offline,
        },
        "stages": {},
        "coverage": {},
        "top": [],
    }
    stage_results: dict[str, dict] = {}

    if "matrix" in stages:
        stage_results["matrix"] = stage_matrix(
            paths,
            query_budget=args.query_budget,
            pages=args.search_pages,
            offline=args.offline,
            quiet=args.quiet,
        )
        payload["stages"]["matrix"] = {"summary": stage_results["matrix"]}
    if "sources" in stages:
        stage_results["sources"] = expand_sources(paths)
        payload["stages"]["sources"] = {"summary": stage_results["sources"]}
    if "collect" in stages:
        collected = stage_collect(
            paths,
            workers=args.workers,
            timeout=args.timeout,
            refresh_hours=args.refresh_hours,
            deadline=Deadline(max(1.0, args.budget_seconds * args.collect_share))
            if args.budget_seconds > 0
            else deadline,
            quiet=args.quiet,
        )
        stage_results["collect"] = collected
        payload["stages"]["collect"] = {"summary": collected}
        payload["partial"] = payload["partial"] or collected.get("partial", False)
    if "probe" in stages:
        tcp = stage_tcp(
            paths,
            concurrency=args.tcp_concurrency,
            timeout=args.tcp_timeout,
            batch_limit=args.probe_budget,
            deadline=deadline,
            quiet=args.quiet,
        )
        http = stage_http(
            paths,
            probe_url=args.probe_url,
            concurrency=args.probe_concurrency,
            timeout=args.timeout,
            batch_limit=args.probe_budget,
            deadline=deadline,
            quiet=args.quiet,
        )
        stage_results["probe"] = {"tcp": tcp, "http": http}
        payload["stages"]["probe"] = {"summary": stage_results["probe"]}
        payload["partial"] = payload["partial"] or tcp.get("partial", False) or http.get(
            "partial", False
        )
    if "rank" in stages:
        ranked = stage_rank(
            paths,
            probe_url=args.probe_url,
            top=args.top,
            verify=args.verify,
            timeout=args.timeout,
            quiet=args.quiet,
        )
        stage_results["rank"] = ranked
        payload["stages"]["rank"] = {"summary": {k: v for k, v in ranked.items() if k != "top"}}
        payload["top"] = ranked["top"]

    payload["finished_at"] = iso()
    payload["coverage"] = coverage_snapshot(paths)
    # Coverage is only complete when the matrix cursor wrapped and every pooled
    # candidate has a verdict; anything less is reported as partial.
    if (
        payload["coverage"]["candidate_coverage"] < 1.0
        or payload["coverage"]["matrix_coverage"] < 1.0
    ):
        payload["partial"] = True
    write_json(
        paths.top_json,
        {
            "schema": TOP_SCHEMA,
            "generated_at": payload["finished_at"],
            "run_id": identifier,
            "probe_url": args.probe_url,
            "partial": payload["partial"],
            "coverage": payload["coverage"],
            "top": payload["top"],
            "list": [entry["proxy"] for entry in payload["top"]],
            "report": _repo_path(paths.report),
        },
    )
    paths.report.write_text(render_report(payload), encoding="utf-8")
    write_json(paths.state, payload)

    if "sync" in stages and args.sync:
        payload["stages"]["sync"] = {"summary": stage_sync(paths, branch=args.branch)}
    elif "sync" in stages:
        payload["stages"]["sync"] = {"summary": {"status": "skipped", "reason": "--sync not given"}}
        write_json(paths.state, payload)
    if not args.quiet:
        print(
            f"scan {identifier}: {payload['coverage']['candidates']} candidates, "
            f"{payload['coverage']['http_alive']} http-alive, "
            f"{len(payload['top'])} in top {args.top}, sync="
            f"{payload['stages'].get('sync', {}).get('summary', {}).get('status', 'off')}"
        )
    return payload



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("scan", help="run the full proxy scan flow")
    run.set_defaults(command="scan")
    run.add_argument(
        "--stages",
        default="all",
        help=f"comma separated subset of {','.join(STAGE_ORDER)} (default all)",
    )
    run.add_argument("--index-dir", default=str(INDEX), help="index directory")
    run.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_SECONDS)
    run.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    run.add_argument("--tcp-concurrency", type=int, default=512)
    run.add_argument("--tcp-timeout", type=float, default=3.0)
    run.add_argument("--probe-concurrency", type=int, default=DEFAULT_PROBE_CONCURRENCY)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument(
        "--probe-budget",
        type=int,
        default=DEFAULT_PROBE_BUDGET,
        help="per-run cap on candidates probed (0 = time budget only)",
    )
    run.add_argument("--query-budget", type=int, default=DEFAULT_QUERY_BUDGET)
    run.add_argument("--search-pages", type=int, default=2)
    run.add_argument("--refresh-hours", type=float, default=6.0)
    run.add_argument(
        "--collect-share",
        type=float,
        default=0.45,
        help="fraction of the time budget reserved for source collection",
    )
    run.add_argument("--probe-url", default=DEFAULT_PROBE_URL)
    run.add_argument("--top", type=int, default=DEFAULT_TOP)
    run.add_argument("--verify", type=int, default=60, help="candidates re-probed before ranking")
    run.add_argument("--sync", action="store_true", help="commit and push the result set")
    run.add_argument("--branch", default="main", help="target branch for --sync")
    run.add_argument("--offline", action="store_true", help="skip GitHub discovery")
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--json", action="store_true", dest="as_json")

    top = sub.add_parser("top", help="print the current published top list")
    top.set_defaults(command="top")
    top.add_argument("--index-dir", default=str(INDEX))
    top.add_argument("--json", action="store_true", dest="as_json")

    status = sub.add_parser("status", help="print the last run summary")
    status.set_defaults(command="status")
    status.add_argument("--index-dir", default=str(INDEX))
    status.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "scan"):
        if args.command is None:
            args = parser.parse_args(["scan", *([] if argv is None else argv)])
        try:
            payload = scan(args)
        except KeyboardInterrupt:  # pragma: no cover - operator convenience
            return 130
        if getattr(args, "as_json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if not payload.get("error") else 1
    paths = IndexPaths(args.index_dir)
    if args.command == "top":
        payload = read_json(paths.top_json, {})
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for entry in payload.get("top") or []:
                print(f"{entry['proxy']}\t{entry['latency_ms']}ms")
        return 0
    state = read_json(paths.state, {})
    if args.as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(
            f"run {state.get('run_id', '-')} at {state.get('finished_at', '-')} "
            f"partial={state.get('partial')} top={len((state.get('top') or []))}"
        )
        for stage, result in (state.get("stages") or {}).items():
            print(f"  {stage}: {json.dumps(result.get('summary'), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

