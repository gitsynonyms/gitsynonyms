#!/usr/bin/env python3
"""Crawl seed links level by level and record links, edges, and proxy endpoints.

Level 1 holds the seed URLs (一级种子). A URL first discovered on a level-n page
is stored at level n+1 (a 一级反向链接 when it is found on a level-1 seed), and
that URL becomes the seed for the next hop. The crawl stops as soon as one whole
level discovers no URL that is not already in the SQLite database.

Only the Python standard library is required; the GitHub Actions test job and
the standalone proxy index already run without extra packages.
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Sequence
from urllib import robotparser
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

USER_AGENT = "gitsynonyms-seed-crawler/1.0 (+https://github.com/gitsynonyms/gitsynonyms)"

# Links ending in these suffixes are recorded in the graph but never fetched:
# they are assets, not pages that can lead to another page.
ASSET_SUFFIXES = {
    ".7z", ".avi", ".bmp", ".css", ".doc", ".docx", ".eot", ".gif", ".gz", ".ico",
    ".jpeg", ".jpg", ".js", ".m4a", ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg",
    ".mpg", ".otf", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".svg", ".tar", ".tgz",
    ".tif", ".tiff", ".ttf", ".wav", ".webm", ".webp", ".woff", ".woff2", ".xls",
    ".xlsx", ".xz", ".zip",
}

IPV4 = r"(?:\d{1,3}\.){3}\d{1,3}"
IP_LITERAL = re.compile(rf"^{IPV4}$")
PROXY_PATTERN = re.compile(
    rf"(?:(?P<scheme>https?|socks4a?|socks5h?)://)?"
    rf"(?<![\w.-])(?P<host>{IPV4}):(?P<port>\d{{2,5}})(?!\d)",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:!?)]}'\">"

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    url TEXT PRIMARY KEY,
    level INTEGER NOT NULL,
    kind TEXT NOT NULL,
    parent TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    http_status INTEGER,
    content_type TEXT,
    title TEXT,
    error TEXT,
    first_seen TEXT NOT NULL,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS nodes_level_status ON nodes(level, status);
CREATE TABLE IF NOT EXISTS edges (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    level INTEGER NOT NULL,
    PRIMARY KEY (src, dst)
);
CREATE TABLE IF NOT EXISTS proxies (
    scheme TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    level INTEGER NOT NULL,
    source TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (scheme, host, port)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class FetchResult:
    status: int = 0
    content_type: str = ""
    text: str = ""
    final_url: str = ""
    error: str | None = None
    blocked_by_robots: bool = False


@dataclass
class Page:
    links: set[str] = field(default_factory=set)
    proxies: set[tuple[str, str, int]] = field(default_factory=set)
    title: str = ""


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http_fetch(
    url: str, *, timeout: float = 20.0, max_bytes: int = 2_000_000
) -> FetchResult:
    """Fetch one URL with a byte cap and identity encoding (no gzip handling)."""
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/json,"
                "text/plain;q=0.9,*/*;q=0.5"
            ),
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type() or ""
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read(max_bytes)
            return FetchResult(
                status=int(getattr(response, "status", 200)),
                content_type=content_type,
                text=payload.decode(charset, "replace"),
                final_url=response.geturl(),
            )
    except HTTPError as error:
        return FetchResult(status=int(error.code), error=f"HTTP {error.code}")
    except Exception as error:  # noqa: BLE001 - one dead URL must not stop the crawl
        return FetchResult(error=f"{type(error).__name__}: {error}")


def normalize_url(base: str, href: str) -> str | None:
    """Resolve href against base, drop fragments, and reject non-HTTP schemes."""
    href = (href or "").strip()
    if not href or href.startswith(
        ("#", "mailto:", "javascript:", "tel:", "data:", "about:", "blob:")
    ):
        return None
    try:
        absolute = urljoin(base, href)
        parts = urlsplit(absolute)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def is_crawlable(url: str, *, allow_ip_literals: bool = False) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not host:
        return False
    if not allow_ip_literals and IP_LITERAL.fullmatch(host):
        return False
    return Path(parts.path).suffix.lower() not in ASSET_SUFFIXES


def parse_proxies(text: str) -> set[tuple[str, str, int]]:
    """Collect scheme://ip:port and bare ip:port endpoints from arbitrary text."""
    found: set[tuple[str, str, int]] = set()
    for match in PROXY_PATTERN.finditer(text):
        octets = [int(part) for part in match.group("host").split(".")]
        if any(part > 255 for part in octets) or octets[0] == 0:
            continue
        port = int(match.group("port"))
        if not 1 <= port <= 65535:
            continue
        scheme = (match.group("scheme") or "http").lower()
        found.add((scheme, match.group("host"), port))
    return found


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"a", "area"}:
            for name, value in attrs:
                if name == "href" and value:
                    self.links.append(value)
                    break
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def extract_page(text: str, base_url: str, content_type: str = "") -> Page:
    """Extract crawlable links, proxy endpoints, and the page title."""
    page = Page(proxies=parse_proxies(text))
    lowered = text[:5000].lower()
    looks_html = (
        "html" in content_type.lower() or "<html" in lowered or "<a " in lowered
    )
    if looks_html:
        parser = _LinkParser()
        try:
            parser.feed(text)
        except Exception:  # noqa: BLE001 - malformed markup is normal on the web
            pass
        page.title = " ".join("".join(parser.title_parts).split())[:300]
        for href in parser.links:
            normalized = normalize_url(base_url, href)
            if normalized:
                page.links.add(normalized)
    for raw in URL_PATTERN.findall(text):
        normalized = normalize_url(base_url, raw.rstrip(TRAILING_PUNCTUATION))
        if normalized:
            page.links.add(normalized)
    self_url = normalize_url(base_url, base_url)
    if self_url:
        page.links.discard(self_url)
    return page


def _is_textual(content_type: str) -> bool:
    if not content_type:
        return True
    lowered = content_type.lower()
    if lowered.startswith("text/"):
        return True
    if lowered in {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "application/rss+xml",
        "application/atom+xml",
    }:
        return True
    return lowered.endswith(("+json", "+xml"))


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if str(path.parent) not in {"", "."}:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(SCHEMA)
    return connection


def set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def add_seeds(
    connection: sqlite3.Connection,
    seeds: Sequence[str],
    *,
    refresh: bool = False,
) -> tuple[int, int]:
    """Insert level-1 seeds. Returns (new seeds, seeds already present)."""
    added = 0
    existing = 0
    now = timestamp()
    for seed in seeds:
        normalized = normalize_url(seed, seed)
        if normalized is None:
            raise ValueError(f"Seed is not a valid http(s) URL: {seed!r}")
        cursor = connection.execute(
            "INSERT OR IGNORE INTO nodes(url, level, kind, status, first_seen) "
            "VALUES (?, 1, 'seed', 'pending', ?)",
            (normalized, now),
        )
        if cursor.rowcount:
            added += 1
        else:
            existing += 1
            if refresh:
                connection.execute(
                    "UPDATE nodes SET status='pending', error=NULL, fetched_at=NULL "
                    "WHERE url=? AND status!='pending'",
                    (normalized,),
                )
    connection.commit()
    return added, existing


def _insert_node(
    connection: sqlite3.Connection, url: str, level: int, parent: str
) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO nodes(url, level, kind, parent, status, first_seen) "
        "VALUES (?, ?, 'link', ?, 'pending', ?)",
        (url, level, parent, timestamp()),
    )
    return cursor.rowcount == 1


def _insert_proxy(
    connection: sqlite3.Connection,
    proxy: tuple[str, str, int],
    level: int,
    source: str,
) -> bool:
    scheme, host, port = proxy
    cursor = connection.execute(
        "INSERT OR IGNORE INTO proxies(scheme, host, port, level, source, first_seen) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (scheme, host, port, level, source, timestamp()),
    )
    return cursor.rowcount == 1


def report(db_path: str | Path) -> dict:
    """Summarize the stored graph without changing it."""
    connection = connect(db_path)
    proxy_counts = dict(
        connection.execute("SELECT level, COUNT(*) FROM proxies GROUP BY level")
    )
    levels = []
    for row in connection.execute(
        "SELECT level, SUM(kind='seed') AS seeds, SUM(kind='link') AS links, "
        "SUM(status='done') AS done, SUM(status='error') AS errors, "
        "SUM(status='skipped') AS skipped, SUM(status='pending') AS pending, "
        "COUNT(*) AS total FROM nodes GROUP BY level ORDER BY level"
    ):
        levels.append(
            {
                "level": row["level"],
                "total": row["total"],
                "seeds": row["seeds"] or 0,
                "links": row["links"] or 0,
                "done": row["done"] or 0,
                "errors": row["errors"] or 0,
                "skipped": row["skipped"] or 0,
                "pending": row["pending"] or 0,
                "proxies": proxy_counts.get(row["level"], 0),
            }
        )
    totals = {
        "nodes": connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
        "seeds": connection.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='seed'"
        ).fetchone()[0],
        "links": connection.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='link'"
        ).fetchone()[0],
        "proxies": connection.execute("SELECT COUNT(*) FROM proxies").fetchone()[0],
        "edges": connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
    }
    meta = dict(connection.execute("SELECT key, value FROM meta"))
    connection.close()
    return {"levels": levels, "totals": totals, "meta": meta}


def export_nodes(db_path: str | Path, path: str | Path) -> int:
    """Write every node as JSON Lines with its level, role, and hop count."""
    destination = Path(path)
    if str(destination.parent) not in {"", "."}:
        destination.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    count = 0
    with destination.open("w", encoding="utf-8") as handle:
        for row in connection.execute(
            "SELECT url, level, kind, parent, status, http_status, title, error, "
            "first_seen, fetched_at FROM nodes ORDER BY level, url"
        ):
            record = dict(row)
            record["role"] = "seed" if row["kind"] == "seed" else "backlink"
            record["hop"] = max(0, row["level"] - 1)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    connection.close()
    return count


def export_proxies(db_path: str | Path, path: str | Path) -> int:
    """Write unique proxy endpoints as scheme://host:port, ordered by level."""
    destination = Path(path)
    if str(destination.parent) not in {"", "."}:
        destination.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    count = 0
    with destination.open("w", encoding="utf-8") as handle:
        for row in connection.execute(
            "SELECT scheme, host, port FROM proxies ORDER BY level, host, port"
        ):
            handle.write(f"{row['scheme']}://{row['host']}:{row['port']}\n")
            count += 1
    connection.close()
    return count


def crawl(
    seeds: Sequence[str],
    db_path: str | Path,
    *,
    fetcher=http_fetch,
    max_depth: int = 3,
    max_pages: int = 200,
    workers: int = 4,
    delay: float = 0.0,
    respect_robots: bool = True,
    allow_ip_literals: bool = False,
    allow_pattern: str | None = None,
    deny_pattern: str | None = None,
    refresh: bool = False,
    progress=lambda message: print(message, flush=True),
) -> dict:
    """Walk the seed graph level by level until a whole level adds no new URL."""
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    allow = re.compile(allow_pattern) if allow_pattern else None
    deny = re.compile(deny_pattern) if deny_pattern else None

    connection = connect(db_path)
    seed_added, seed_existing = add_seeds(connection, seeds, refresh=refresh)
    set_meta(connection, "started_at", timestamp())

    robots_cache: dict[str, robotparser.RobotFileParser] = {}
    robots_lock = threading.Lock()

    def robots_allowed(url: str) -> bool:
        origin = origin_of(url)
        with robots_lock:
            parser = robots_cache.get(origin)
            if parser is None:
                result = fetcher(origin + "/robots.txt")
                parser = robotparser.RobotFileParser()
                lines = (
                    result.text.splitlines()
                    if result.status == 200 and result.text.strip()
                    else []
                )
                parser.parse(lines)
                robots_cache[origin] = parser
            return parser.can_fetch(USER_AGENT, url)

    def fetch_node(url: str) -> tuple[str, FetchResult]:
        if respect_robots and not robots_allowed(url):
            return url, FetchResult(blocked_by_robots=True)
        if delay > 0:
            time.sleep(delay)
        return url, fetcher(url)

    pages_fetched = 0
    levels: dict[int, dict[str, int]] = {}
    stop_reason = "converged"
    max_level = 0
    new_nodes = 0
    new_proxies = 0

    while True:
        row = connection.execute(
            "SELECT MIN(level) FROM nodes WHERE status='pending'"
        ).fetchone()
        if row is None or row[0] is None:
            stop_reason = "converged"
            break
        level = int(row[0])
        if level > max_depth:
            connection.execute(
                "UPDATE nodes SET status='skipped', error='beyond max-depth' "
                "WHERE status='pending' AND level > ?",
                (max_depth,),
            )
            connection.commit()
            stop_reason = "max-depth"
            break
        remaining = max_pages - pages_fetched
        if remaining <= 0:
            stop_reason = "max-pages"
            break
        batch = list(
            connection.execute(
                "SELECT url FROM nodes WHERE level=? AND status='pending' "
                "ORDER BY first_seen, url LIMIT ?",
                (level, remaining),
            )
        )
        if not batch:
            continue
        pages_fetched += len(batch)
        max_level = max(max_level, level)
        bucket = levels.setdefault(
            level, {"seeds": 0, "links": 0, "proxies": 0, "errors": 0}
        )
        bucket["seeds" if level == 1 else "links"] += len(batch)

        progress(f"level {level}: fetching {len(batch)} page(s) ...")
        with ThreadPoolExecutor(max_workers=min(workers, len(batch))) as pool:
            results = list(pool.map(fetch_node, [item["url"] for item in batch]))

        added_links = 0
        blocked_by_depth = 0
        for url, result in results:
            now = timestamp()
            if result.blocked_by_robots:
                connection.execute(
                    "UPDATE nodes SET status='skipped', error='blocked by robots.txt', "
                    "fetched_at=? WHERE url=?",
                    (now, url),
                )
                continue
            if result.error or result.status == 0 or result.status >= 400:
                connection.execute(
                    "UPDATE nodes SET status='error', http_status=?, error=?, "
                    "fetched_at=? WHERE url=?",
                    (
                        result.status or None,
                        (result.error or f"HTTP {result.status}")[:500],
                        now,
                        url,
                    ),
                )
                bucket["errors"] += 1
                continue
            if not _is_textual(result.content_type):
                connection.execute(
                    "UPDATE nodes SET status='skipped', http_status=?, content_type=?, "
                    "error='non-text content', fetched_at=? WHERE url=?",
                    (result.status, result.content_type, now, url),
                )
                continue
            page_url = result.final_url or url
            page = extract_page(result.text, page_url, result.content_type)
            connection.execute(
                "UPDATE nodes SET status='done', http_status=?, content_type=?, "
                "title=?, error=NULL, fetched_at=? WHERE url=?",
                (result.status, result.content_type, page.title or None, now, url),
            )

            for link in sorted(page.links):
                if not is_crawlable(link, allow_ip_literals=allow_ip_literals):
                    continue
                if allow is not None and not allow.search(link):
                    continue
                if deny is not None and deny.search(link):
                    continue
                if level >= max_depth:
                    known = connection.execute(
                        "SELECT 1 FROM nodes WHERE url=?", (link,)
                    ).fetchone()
                    if known is None:
                        blocked_by_depth += 1
                    continue
                if _insert_node(connection, link, level + 1, url):
                    added_links += 1
                    new_nodes += 1
                connection.execute(
                    "INSERT OR IGNORE INTO edges(src, dst, level) VALUES (?, ?, ?)",
                    (url, link, level + 1),
                )
            for proxy in page.proxies:
                if _insert_proxy(connection, proxy, level, url):
                    bucket["proxies"] += 1
                    new_proxies += 1
        connection.commit()

        if blocked_by_depth and not added_links:
            stop_reason = "max-depth"
            break
        if added_links == 0:
            deeper = connection.execute(
                "SELECT 1 FROM nodes WHERE status='pending' AND level > ? LIMIT 1",
                (level,),
            ).fetchone()
            if deeper is None:
                stop_reason = "converged"
                break
        if pages_fetched >= max_pages:
            stop_reason = "max-pages"
            break

    set_meta(connection, "finished_at", timestamp())
    set_meta(connection, "stop_reason", stop_reason)
    set_meta(connection, "max_level", str(max_level))
    set_meta(connection, "pages_fetched", str(pages_fetched))
    set_meta(connection, "converged", "yes" if stop_reason == "converged" else "no")
    connection.commit()
    summary = {
        "seeds": len(seeds),
        "seed_added": seed_added,
        "seed_existing": seed_existing,
        "levels": [
            {"level": level, **values} for level, values in sorted(levels.items())
        ],
        "max_level": max_level,
        "pages_fetched": pages_fetched,
        "new_nodes": new_nodes,
        "new_proxies": new_proxies,
        "converged": stop_reason == "converged",
        "stop_reason": stop_reason,
        "db": str(db_path),
    }
    connection.close()
    return summary


def print_summary(summary: dict) -> None:
    print(f"database: {summary['db']}")
    print(f"seeds: {summary['seeds']} ({summary['seed_added']} new, "
          f"{summary['seed_existing']} already stored)")
    print(f"{'level':>5}  {'seeds':>5}  {'links':>5}  {'proxies':>7}  {'errors':>6}")
    for row in summary["levels"]:
        print(
            f"{row['level']:>5}  {row['seeds']:>5}  {row['links']:>5}  "
            f"{row['proxies']:>7}  {row['errors']:>6}"
        )
    print(
        f"stop: {summary['stop_reason']} after {summary['pages_fetched']} page(s), "
        f"max level {summary['max_level']}"
    )
    print(
        f"new this run: {summary['new_nodes']} link node(s), "
        f"{summary['new_proxies']} proxy endpoint(s)"
    )
    if summary["converged"]:
        print("every link found on the last level was already in the database")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl seed links level by level, store every link, edge, and proxy "
            "endpoint in SQLite, and stop when a level adds no new URL."
        )
    )
    parser.add_argument("--seed", action="append", default=[], help="Seed URL (repeatable).")
    parser.add_argument(
        "--seed-file",
        help="Text file with one seed URL per line; blank lines and # comments are ignored.",
    )
    parser.add_argument(
        "--db",
        default="proxyindex/seed-graph.sqlite3",
        help="SQLite database path (default: proxyindex/seed-graph.sqlite3).",
    )
    parser.add_argument("--max-depth", type=int, default=3, help="Deepest level to fetch (default: 3).")
    parser.add_argument("--max-pages", type=int, default=200, help="Page budget for this run (default: 200).")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent fetches (default: 4).")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to sleep before each fetch (default: 0).")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds (default: 20).")
    parser.add_argument("--max-bytes", type=int, default=2_000_000, help="Maximum bytes read per page (default: 2 MB).")
    parser.add_argument("--allow-ip", action="store_true", help="Also crawl http(s) links whose host is an IP literal.")
    parser.add_argument("--allow-regex", help="Only queue links matching this regular expression.")
    parser.add_argument("--deny-regex", help="Never queue links matching this regular expression.")
    parser.add_argument("--ignore-robots", action="store_true", help="Do not honour robots.txt.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch seeds that are already stored instead of resuming after them.",
    )
    parser.add_argument("--export-nodes", help="Write every node with level/role/hop as JSON Lines.")
    parser.add_argument("--export-proxies", help="Write unique proxies as scheme://host:port.")
    parser.add_argument("--report-only", action="store_true", help="Print the stored graph summary and exit.")
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    args = parser.parse_args(argv)

    if args.report_only:
        stored = report(args.db)
        print(json.dumps(stored, indent=2, ensure_ascii=False))
        return 0

    seeds = list(args.seed)
    if args.seed_file:
        seed_path = Path(args.seed_file)
        if not seed_path.is_file():
            parser.error(f"--seed-file does not exist: {seed_path}")
        for line in seed_path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                seeds.append(line)
    seeds = list(dict.fromkeys(seeds))
    if not seeds:
        parser.error("provide at least one --seed or --seed-file")

    fetcher = functools.partial(
        http_fetch, timeout=args.timeout, max_bytes=args.max_bytes
    )
    try:
        summary = crawl(
            seeds,
            args.db,
            fetcher=fetcher,
            max_depth=args.max_depth,
            max_pages=args.max_pages,
            workers=args.workers,
            delay=args.delay,
            respect_robots=not args.ignore_robots,
            allow_ip_literals=args.allow_ip,
            allow_pattern=args.allow_regex,
            deny_pattern=args.deny_regex,
            refresh=args.refresh,
        )
    except (ValueError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.export_nodes:
        count = export_nodes(args.db, args.export_nodes)
        print(f"exported {count} node(s) to {args.export_nodes}")
    if args.export_proxies:
        count = export_proxies(args.db, args.export_proxies)
        print(f"exported {count} proxy endpoint(s) to {args.export_proxies}")
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
