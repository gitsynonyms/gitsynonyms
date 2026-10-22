#!/usr/bin/env python3
"""Discover backlinks level by level until a level adds no new page.

Level 1 is the seed URL set (一级种子). Pages that link to or mention a level-n
URL are stored at level n+1 (a 一级反向链接 for n = 1) and become the seeds for
the next round. The crawl stops as soon as one whole level discovers no page
that is not already in the SQLite database.

Backlinks are discovered through public, key-free providers (Bing, Hacker News,
Reddit via Pullpush, Lemmy, Stack Exchange, GitHub, Wikipedia, GDELT) plus an
optional local edge index and an optional SearXNG instance. Every candidate is
optionally fetched and classified as a real hyperlink, a text mention, or
unverified.
"""

from __future__ import annotations

import argparse
import base64
import csv
import functools
import html
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
from typing import Callable, Sequence
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

USER_AGENT = (
    "gitsynonyms-backlink-graph/1.0 "
    "(+https://github.com/gitsynonyms/gitsynonyms)"
)

# Pages served by a search engine or an API are discovery tools, never backlinks.
ENGINE_HOSTS = {
    "bing.com",
    "www.bing.com",
    "google.com",
    "www.google.com",
    "duckduckgo.com",
    "html.duckduckgo.com",
    "lite.duckduckgo.com",
    "search.brave.com",
    "mojeek.com",
    "www.mojeek.com",
    "yandex.com",
    "yandex.ru",
    "baidu.com",
    "www.baidu.com",
    "hn.algolia.com",
    "api.github.com",
    "api.stackexchange.com",
    "api.pullpush.io",
    "api.gdeltproject.org",
}

ASSET_SUFFIXES = {
    ".7z", ".avi", ".bmp", ".css", ".doc", ".docx", ".eot", ".gif", ".gz", ".ico",
    ".jpeg", ".jpg", ".js", ".m4a", ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg",
    ".mpg", ".otf", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".svg", ".tar", ".tgz",
    ".tif", ".tiff", ".ttf", ".wav", ".webm", ".webp", ".woff", ".woff2", ".xls",
    ".xlsx", ".xz", ".zip",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    url TEXT PRIMARY KEY,
    level INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target TEXT,
    provider TEXT,
    relation TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    http_status INTEGER,
    error TEXT,
    first_seen TEXT NOT NULL,
    queried_at TEXT
);
CREATE INDEX IF NOT EXISTS nodes_level_status ON nodes(level, status);
CREATE TABLE IF NOT EXISTS edges (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    level INTEGER NOT NULL,
    provider TEXT NOT NULL,
    relation TEXT NOT NULL,
    PRIMARY KEY (source, target, provider)
);
CREATE TABLE IF NOT EXISTS queries (
    target TEXT NOT NULL,
    provider TEXT NOT NULL,
    level INTEGER NOT NULL,
    status TEXT NOT NULL,
    results INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (target, provider)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

GENERATIONS = "零一二三四五六七八九十"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def chinese_number(value: int) -> str:
    return GENERATIONS[value] if 0 <= value < len(GENERATIONS) else str(value)


def level_label(level: int, kind: str) -> str:
    if kind == "seed":
        return f"{chinese_number(level)}级种子"
    if level <= 1:
        return ""
    return f"{chinese_number(level - 1)}级反向链接"


@dataclass
class FetchResult:
    status: int = 0
    content_type: str = ""
    text: str = ""
    final_url: str = ""
    error: str | None = None


@dataclass
class Backlink:
    url: str
    title: str = ""
    relation: str = "mention"


@dataclass
class ProviderResult:
    provider: str
    backlinks: list[Backlink] = field(default_factory=list)
    error: str | None = None


class RateLimiter:
    """Space out requests so free endpoints are not hammered."""

    def __init__(self, delay: float) -> None:
        self.delay = max(0.0, delay)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self.delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            pause = self._next - now
            if pause > 0:
                time.sleep(pause)
            self._next = max(now, self._next) + self.delay


@dataclass
class ProviderOptions:
    fetcher: Callable[[str], FetchResult] = None  # type: ignore[assignment]
    limiter: RateLimiter = field(default_factory=lambda: RateLimiter(0.5))
    limit: int = 20
    workers: int = 4
    searx_url: str | None = None
    lemmy_instance: str = "https://lemmy.world"
    se_sites: tuple[str, ...] = ("stackoverflow",)
    wikipedia_lang: str = "en"
    index: dict[str, list[Backlink]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.fetcher is None:
            self.fetcher = http_fetch

    def fetch(self, url: str) -> FetchResult:
        self.limiter.wait()
        return self.fetcher(url)

    def get_json(self, url: str) -> tuple[object | None, str | None]:
        result = self.fetch(url)
        if result.error or result.status == 0 or result.status >= 400:
            return None, result.error or f"HTTP {result.status}"
        try:
            return json.loads(result.text), None
        except json.JSONDecodeError as error:
            return None, f"invalid JSON: {error}"


def http_fetch(
    url: str, *, timeout: float = 20.0, max_bytes: int = 2_000_000
) -> FetchResult:
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
    except Exception as error:  # noqa: BLE001 - one dead source must not stop the crawl
        return FetchResult(error=f"{type(error).__name__}: {error}")


def normalize_url(base: str, href: str) -> str | None:
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


def same_target(left: str, right: str) -> bool:
    """Compare two URLs ignoring scheme, a leading www., and a trailing slash."""
    first, second = urlsplit(left), urlsplit(right)
    host_a = (first.hostname or "").lower()
    host_b = (second.hostname or "").lower()
    if host_a.startswith("www."):
        host_a = host_a[4:]
    if host_b.startswith("www."):
        host_b = host_b[4:]
    return (
        host_a == host_b
        and first.path.rstrip("/") == second.path.rstrip("/")
        and first.query == second.query
    )


def _clean_title(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())[:300]


def _unwrap_bing(href: str) -> str | None:
    href = html.unescape(href)
    parsed = urlsplit(href)
    if not (parsed.hostname or "").endswith("bing.com"):
        return href
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if not encoded.startswith("a1"):
        return None
    payload = encoded[2:]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - malformed redirect payload
        return None
    return decoded if decoded.startswith(("http://", "https://")) else None


class _PageParser(HTMLParser):
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


def _parse_page(text: str) -> tuple[list[str], str]:
    parser = _PageParser()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 - malformed markup is normal on the web
        pass
    return parser.links, " ".join("".join(parser.title_parts).split())[:300]


def _mentions(text: str, target: str) -> bool:
    """Cheap pre-filter: does the text contain the URL (or its host/path)?"""
    if not text:
        return False
    plain = html.unescape(re.sub(r"<[^>]+>", " ", text))
    parsed = urlsplit(target)
    variants = {
        target,
        target.rstrip("/"),
        f"{parsed.netloc}{parsed.path}",
        f"//{parsed.netloc}{parsed.path}",
    }
    return any(variant and variant in plain for variant in variants)


def _link_matches(candidate: str, target: str) -> bool:
    normalized = normalize_url(candidate, candidate)
    return bool(normalized and same_target(normalized, target))


def bing(target: str, options: ProviderOptions) -> ProviderResult:
    """Bing web results for the exact URL (news, forums, blogs, social pages)."""
    query = urlencode({"q": f'"{target}"', "count": str(min(50, options.limit))})
    result = options.fetch("https://www.bing.com/search?" + query)
    if result.error or result.status >= 400:
        return ProviderResult("bing", error=result.error or f"HTTP {result.status}")
    lowered = result.text.lower()
    if "captcha" in lowered or "unusual traffic" in lowered:
        return ProviderResult("bing", error="blocked by Bing (captcha)")
    found: list[Backlink] = []
    seen: set[str] = set()
    for block in re.findall(r'<li class="b_algo[^"]*".*?</li>', result.text, re.S):
        match = re.search(
            r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S
        )
        if not match:
            continue
        url = _unwrap_bing(match.group(1))
        if not url or url in seen:
            continue
        seen.add(url)
        found.append(Backlink(url, _clean_title(match.group(2)), "mention"))
    return ProviderResult("bing", found)


def hackernews(target: str, options: ProviderOptions) -> ProviderResult:
    """Hacker News stories that link to the URL, plus comments that mention it."""
    found: list[Backlink] = []
    seen: set[str] = set()
    errors: list[str] = []
    queries = (
        (
            "story",
            {
                "query": target,
                "tags": "story",
                "restrictSearchableAttributes": "url",
                "hitsPerPage": str(min(100, options.limit)),
            },
        ),
        (
            "comment",
            {
                "query": target,
                "tags": "comment",
                "hitsPerPage": str(min(100, options.limit)),
            },
        ),
    )
    for tag, params in queries:
        payload, error = options.get_json(
            "https://hn.algolia.com/api/v1/search?" + urlencode(params)
        )
        if error:
            errors.append(f"{tag}: {error}")
            continue
        if not isinstance(payload, dict):
            continue
        for hit in payload.get("hits", []):
            item_id = str(hit.get("objectID") or "")
            if not item_id:
                continue
            if tag == "story":
                if not _link_matches(str(hit.get("url") or ""), target):
                    continue
                relation = "hyperlink"
                title = hit.get("title") or ""
            else:
                if not _mentions(str(hit.get("comment_text") or ""), target):
                    continue
                relation = "mention"
                title = hit.get("story_title") or ""
            page = f"https://news.ycombinator.com/item?id={item_id}"
            if page in seen:
                continue
            seen.add(page)
            found.append(Backlink(page, str(title), relation))
    return ProviderResult("hackernews", found, "; ".join(errors) or None)


def reddit(target: str, options: ProviderOptions) -> ProviderResult:
    """Reddit submissions and comments through the Pullpush archive API."""
    found: list[Backlink] = []
    seen: set[str] = set()
    errors: list[str] = []
    for kind in ("submission", "comment"):
        url = (
            f"https://api.pullpush.io/reddit/search/{kind}/?"
            + urlencode({"q": target, "size": str(min(100, options.limit))})
        )
        payload, error = options.get_json(url)
        if error:
            errors.append(f"{kind}: {error}")
            continue
        if not isinstance(payload, dict):
            continue
        for item in payload.get("data", []):
            permalink = str(item.get("permalink") or "")
            if not permalink:
                continue
            if kind == "submission":
                submitted = str(item.get("url") or "")
                if submitted and _link_matches(submitted, target):
                    relation = "hyperlink"
                    title = item.get("title") or ""
                elif _mentions(str(item.get("selftext") or ""), target):
                    relation = "mention"
                    title = item.get("title") or ""
                else:
                    continue
            else:
                body = str(item.get("body") or "")
                if not _mentions(body, target):
                    continue
                relation = "mention"
                title = " ".join(body.split())[:160]
            page = "https://www.reddit.com" + (
                permalink if permalink.startswith("/") else "/" + permalink
            )
            if page in seen:
                continue
            seen.add(page)
            found.append(Backlink(page, str(title or "Reddit"), relation))
    return ProviderResult("reddit", found, "; ".join(errors) or None)


def lemmy(target: str, options: ProviderOptions) -> ProviderResult:
    """Lemmy posts and comments on one instance (federated URLs are kept)."""
    url = (
        options.lemmy_instance.rstrip("/")
        + "/api/v3/search?"
        + urlencode({"q": target, "type_": "All", "limit": str(min(50, options.limit))})
    )
    payload, error = options.get_json(url)
    if error:
        return ProviderResult("lemmy", error=error)
    if not isinstance(payload, dict):
        return ProviderResult("lemmy", error="unexpected response")
    found: list[Backlink] = []
    seen: set[str] = set()
    for entry in payload.get("posts", []):
        post = entry.get("post", entry)
        page = post.get("ap_id") or post.get("url")
        if not page or page in seen:
            continue
        submitted = str(post.get("url") or "")
        content = str(post.get("body") or "")
        if submitted and _link_matches(submitted, target):
            relation = "hyperlink"
        elif _mentions(f"{post.get('name') or ''} {content}", target):
            relation = "mention"
        else:
            continue
        seen.add(page)
        found.append(Backlink(str(page), str(post.get("name") or ""), relation))
    for entry in payload.get("comments", []):
        comment = entry.get("comment", entry)
        page = comment.get("ap_id")
        if not page or page in seen:
            continue
        body = str(comment.get("content") or "")
        if not _mentions(body, target):
            continue
        seen.add(page)
        found.append(
            Backlink(str(page), " ".join(body.split())[:160] or "Lemmy comment", "mention")
        )
    return ProviderResult("lemmy", found)


def stackexchange(target: str, options: ProviderOptions) -> ProviderResult:
    """Q&A posts on the configured Stack Exchange sites."""
    found: list[Backlink] = []
    seen: set[str] = set()
    errors: list[str] = []
    for site in options.se_sites:
        url = (
            "https://api.stackexchange.com/2.3/search/advanced?"
            + urlencode(
                {
                    "order": "desc",
                    "sort": "relevance",
                    "q": target,
                    "site": site,
                    "pagesize": str(min(100, options.limit)),
                    "filter": "withbody",
                }
            )
        )
        payload, error = options.get_json(url)
        if error:
            errors.append(f"{site}: {error}")
            continue
        if not isinstance(payload, dict):
            continue
        for item in payload.get("items", []):
            page = item.get("link")
            if not page or page in seen:
                continue
            if not _mentions(str(item.get("body") or ""), target):
                continue
            seen.add(page)
            found.append(Backlink(str(page), str(item.get("title") or ""), "mention"))
    return ProviderResult("stackexchange", found, "; ".join(errors) or None)


def github(target: str, options: ProviderOptions) -> ProviderResult:
    """Issues and pull requests that quote the URL (unauthenticated search)."""
    query = urlencode({"q": f'"{target}" in:body', "per_page": str(min(100, options.limit))})
    payload, error = options.get_json("https://api.github.com/search/issues?" + query)
    if error:
        return ProviderResult("github", error=error)
    if not isinstance(payload, dict):
        return ProviderResult("github", error="unexpected response")
    found = [
        Backlink(str(item["html_url"]), str(item.get("title") or ""), "mention")
        for item in payload.get("items", [])
        if item.get("html_url")
    ]
    return ProviderResult("github", found)


def wikipedia(target: str, options: ProviderOptions) -> ProviderResult:
    """Wikipedia pages whose wiki text cites the URL."""
    lang = options.wikipedia_lang
    query = urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": f'insource:"{target}"',
            "srlimit": str(min(50, options.limit)),
            "format": "json",
        }
    )
    payload, error = options.get_json(f"https://{lang}.wikipedia.org/w/api.php?{query}")
    if error:
        return ProviderResult("wikipedia", error=error)
    if not isinstance(payload, dict):
        return ProviderResult("wikipedia", error="unexpected response")
    found = []
    for item in payload.get("query", {}).get("search", []):
        title = str(item.get("title") or "")
        if not title:
            continue
        page = f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='')}"
        found.append(Backlink(page, title, "hyperlink"))
    return ProviderResult("wikipedia", found)


_GDELT_LOCK = threading.Lock()
_GDELT_LAST = [0.0]
_GDELT_INTERVAL = 5.0


def _gdelt_wait() -> None:
    with _GDELT_LOCK:
        pause = _GDELT_INTERVAL - (time.monotonic() - _GDELT_LAST[0])
        if pause > 0:
            time.sleep(pause)
        _GDELT_LAST[0] = time.monotonic()


def gdelt(target: str, options: ProviderOptions) -> ProviderResult:
    """News articles tracked by GDELT (five seconds between requests)."""
    _gdelt_wait()
    query = urlencode(
        {
            "query": f'"{target}"',
            "mode": "artlist",
            "maxrecords": str(min(250, options.limit)),
            "format": "json",
            "sort": "hybridrel",
        }
    )
    result = options.fetch("https://api.gdeltproject.org/api/v2/doc/doc?" + query)
    if result.error or result.status >= 400:
        return ProviderResult("gdelt", error=result.error or f"HTTP {result.status}")
    try:
        payload = json.loads(result.text)
    except json.JSONDecodeError:
        return ProviderResult("gdelt", error="rate limited or non-JSON response")
    if not isinstance(payload, dict):
        return ProviderResult("gdelt", error="unexpected response")
    found = [
        Backlink(str(article["url"]), str(article.get("title") or ""), "mention")
        for article in payload.get("articles", [])
        if article.get("url")
    ]
    return ProviderResult("gdelt", found)


def searx(target: str, options: ProviderOptions) -> ProviderResult:
    """A user-supplied SearXNG instance with the JSON API enabled."""
    if not options.searx_url:
        return ProviderResult("searx", error="--searx-url is not configured")
    query = urlencode({"q": f'"{target}"', "format": "json"})
    payload, error = options.get_json(options.searx_url.rstrip("/") + "/search?" + query)
    if error:
        return ProviderResult("searx", error=error)
    if not isinstance(payload, dict):
        return ProviderResult("searx", error="unexpected response")
    found = [
        Backlink(str(item["url"]), str(item.get("title") or ""), "mention")
        for item in payload.get("results", [])
        if item.get("url")
    ]
    return ProviderResult("searx", found)


def local_index(target: str, options: ProviderOptions) -> ProviderResult:
    """Backlinks loaded from --index-file (Ahrefs/Moz/Common Crawl exports)."""
    return ProviderResult("index", list(options.index.get(target, [])))


PROVIDERS: dict[str, Callable[[str, ProviderOptions], ProviderResult]] = {
    "bing": bing,
    "hackernews": hackernews,
    "reddit": reddit,
    "lemmy": lemmy,
    "stackexchange": stackexchange,
    "github": github,
    "wikipedia": wikipedia,
    "gdelt": gdelt,
    "searx": searx,
    "index": local_index,
}
DEFAULT_PROVIDERS = (
    "bing",
    "hackernews",
    "reddit",
    "lemmy",
    "stackexchange",
    "github",
    "wikipedia",
    "gdelt",
)


def load_index(path: str | Path) -> dict[str, list[Backlink]]:
    """Load source->target edges from JSON Lines or a two-column CSV file.

    JSON fields: ``source``/``url``/``referrer`` and ``target``; optional
    ``relation`` and ``title`` are preserved. CSV rows are ``source,target``
    with an optional third relation column.
    """
    source_path = Path(path)
    if not source_path.is_file():
        raise ValueError(f"index file not found: {source_path}")
    index: dict[str, list[Backlink]] = {}

    def add(source_value: str, target_value: str, title: str, relation: str) -> None:
        source = normalize_url(source_value, source_value)
        target = normalize_url(target_value, target_value)
        if source and target:
            index.setdefault(target, []).append(Backlink(source, title, relation))

    if source_path.suffix.lower() == ".csv":
        with source_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle):
                if len(row) < 2 or row[0].strip().lower() in {"source", "referrer"}:
                    continue
                relation = row[2].strip() if len(row) > 2 and row[2].strip() else "hyperlink"
                add(row[0].strip(), row[1].strip(), "", relation)
        return index
    for line in source_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        source = str(
            record.get("source") or record.get("url") or record.get("referrer") or ""
        )
        add(
            source,
            str(record.get("target") or ""),
            str(record.get("title") or ""),
            str(record.get("relation") or "hyperlink"),
        )
    return index


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
    added = 0
    existing = 0
    now = timestamp()
    for seed in seeds:
        normalized = normalize_url(seed, seed)
        if normalized is None:
            raise ValueError(f"Seed is not a valid http(s) URL: {seed!r}")
        cursor = connection.execute(
            "INSERT OR IGNORE INTO nodes(url, level, kind, relation, status, first_seen) "
            "VALUES (?, 1, 'seed', 'seed', 'pending', ?)",
            (normalized, now),
        )
        if cursor.rowcount:
            added += 1
        else:
            existing += 1
            if refresh:
                connection.execute(
                    "UPDATE nodes SET status='pending', queried_at=NULL, error=NULL "
                    "WHERE url=?",
                    (normalized,),
                )
    connection.commit()
    return added, existing


def _insert_node(
    connection: sqlite3.Connection,
    url: str,
    level: int,
    target: str,
    provider: str,
    relation: str,
    title: str,
) -> bool:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO nodes("
        "url, level, kind, target, provider, relation, title, status, first_seen"
        ") VALUES (?, ?, 'backlink', ?, ?, ?, ?, 'pending', ?)",
        (url, level, target, provider, relation, title or None, timestamp()),
    )
    return cursor.rowcount == 1


def _insert_edge(
    connection: sqlite3.Connection,
    source: str,
    target: str,
    level: int,
    provider: str,
    relation: str,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO edges(source, target, level, provider, relation) "
        "VALUES (?, ?, ?, ?, ?)",
        (source, target, level, provider, relation),
    )


def _record_query(
    connection: sqlite3.Connection,
    target: str,
    provider: str,
    level: int,
    status: str,
    results: int,
    error: str | None,
) -> None:
    connection.execute(
        "INSERT INTO queries(target, provider, level, status, results, error, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(target, provider) DO UPDATE SET "
        "level=excluded.level, status=excluded.status, results=excluded.results, "
        "error=excluded.error, fetched_at=excluded.fetched_at",
        (target, provider, level, status, results, (error or "")[:500] or None, timestamp()),
    )


def _query_already_ran(connection: sqlite3.Connection, target: str, provider: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM queries WHERE target=? AND provider=?", (target, provider)
        ).fetchone()
        is not None
    )


def report(db_path: str | Path) -> dict:
    connection = connect(db_path)
    levels = [
        dict(row)
        for row in connection.execute(
            "SELECT level, COUNT(*) AS total, SUM(kind='seed') AS seeds, "
            "SUM(kind='backlink') AS backlinks, "
            "SUM(relation='hyperlink') AS hyperlink, "
            "SUM(relation='mention') AS mention, "
            "SUM(relation='unverified') AS unverified, "
            "SUM(status='pending') AS pending "
            "FROM nodes GROUP BY level ORDER BY level"
        )
    ]
    for row in levels:
        row["seed_label"] = level_label(row["level"], "seed")
        row["backlink_label"] = level_label(row["level"], "backlink")
        row["queries"] = connection.execute(
            "SELECT COUNT(*) FROM queries WHERE level=?", (row["level"],)
        ).fetchone()[0]
    totals = {
        "nodes": connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
        "seeds": connection.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='seed'"
        ).fetchone()[0],
        "backlinks": connection.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='backlink'"
        ).fetchone()[0],
        "edges": connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
        "query_rows": connection.execute("SELECT COUNT(*) FROM queries").fetchone()[0],
    }
    meta = dict(connection.execute("SELECT key, value FROM meta"))
    connection.close()
    return {"levels": levels, "totals": totals, "meta": meta}


def export_nodes(db_path: str | Path, path: str | Path) -> int:
    destination = Path(path)
    if str(destination.parent) not in {"", "."}:
        destination.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    count = 0
    with destination.open("w", encoding="utf-8") as handle:
        for row in connection.execute(
            "SELECT url, level, kind, target, provider, relation, title, status, "
            "http_status, error, first_seen, queried_at FROM nodes ORDER BY level, url"
        ):
            record = dict(row)
            record["role"] = "seed" if row["kind"] == "seed" else "backlink"
            record["hop"] = max(0, row["level"] - 1)
            record["label"] = level_label(row["level"], row["kind"])
            record["seed_label"] = level_label(row["level"], "seed")
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    connection.close()
    return count


def export_edges(db_path: str | Path, path: str | Path) -> int:
    destination = Path(path)
    if str(destination.parent) not in {"", "."}:
        destination.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    count = 0
    with destination.open("w", encoding="utf-8") as handle:
        for row in connection.execute(
            "SELECT source, target, level, provider, relation FROM edges "
            "ORDER BY level, target, source"
        ):
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
            count += 1
    connection.close()
    return count


def classify_backlink(
    page_url: str, target: str, options: ProviderOptions
) -> tuple[str, str, int]:
    """Fetch a candidate page and classify it as hyperlink, mention, or unverified."""
    result = options.fetch(page_url)
    if result.error or result.status == 0 or result.status >= 400:
        return "unverified", "", result.status
    links, title = _parse_page(result.text)
    base = result.final_url or page_url
    for href in links:
        normalized = normalize_url(base, href)
        if normalized and same_target(normalized, target):
            return "hyperlink", title, result.status
    text = html.unescape(re.sub(r"<[^>]+>", " ", result.text))
    parsed = urlsplit(target)
    variants = {
        target,
        target.rstrip("/"),
        f"{parsed.netloc}{parsed.path}",
        f"//{parsed.netloc}{parsed.path}",
    }
    if any(variant and variant in text for variant in variants):
        return "mention", title, result.status
    return "unverified", title, result.status


def _host_matches(host: str, candidates: set[str]) -> bool:
    host = host.lower()
    return any(host == item or host.endswith("." + item) for item in candidates)


def _acceptable(
    candidate: str,
    target: str,
    *,
    include_internal: bool,
    allow: re.Pattern | None,
    deny: re.Pattern | None,
) -> bool:
    host = (urlsplit(candidate).hostname or "").lower()
    if not host or _host_matches(host, ENGINE_HOSTS):
        return False
    if same_target(candidate, target):
        return False
    target_host = (urlsplit(target).hostname or "").lower()
    if target_host.startswith("www."):
        target_host = target_host[4:]
    candidate_host = host[4:] if host.startswith("www.") else host
    if not include_internal and candidate_host == target_host:
        return False
    if allow is not None and not allow.search(candidate):
        return False
    if deny is not None and deny.search(candidate):
        return False
    return True


def _run_provider(
    name: str,
    function: Callable[[str, ProviderOptions], ProviderResult],
    target: str,
    options: ProviderOptions,
) -> ProviderResult:
    try:
        return function(target, options)
    except Exception as error:  # noqa: BLE001 - a broken provider must not stop the crawl
        return ProviderResult(name, error=f"{type(error).__name__}: {error}")


def crawl(
    seeds: Sequence[str],
    db_path: str | Path,
    *,
    providers: Sequence[str] | None = None,
    registry: dict[str, Callable[[str, ProviderOptions], ProviderResult]] | None = None,
    options: ProviderOptions | None = None,
    verify: bool = True,
    keep_unverified: bool = False,
    include_internal: bool = False,
    max_depth: int = 2,
    max_queries: int = 20,
    max_pages: int = 200,
    max_verify: int = 40,
    allow_pattern: str | None = None,
    deny_pattern: str | None = None,
    refresh: bool = False,
    progress=lambda message: print(message, flush=True),
) -> dict:
    """Resolve backlinks level by level until a level adds no new page."""
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    if max_queries < 1:
        raise ValueError("max_queries must be at least 1")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    selected = list(providers or DEFAULT_PROVIDERS)
    registry = registry or PROVIDERS
    missing = [name for name in selected if name not in registry]
    if missing:
        raise ValueError(f"unknown provider(s): {', '.join(missing)}")
    options = options or ProviderOptions()
    if options.workers < 1:
        raise ValueError("workers must be at least 1")
    allow = re.compile(allow_pattern) if allow_pattern else None
    deny = re.compile(deny_pattern) if deny_pattern else None

    connection = connect(db_path)
    if refresh:
        connection.execute("DELETE FROM queries")
        connection.execute("UPDATE nodes SET status='pending', queried_at=NULL")
    seed_added, seed_existing = add_seeds(connection, seeds, refresh=refresh)
    node_count = connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    set_meta(connection, "started_at", timestamp())

    levels: dict[int, dict[str, int]] = {}
    targets_queried = 0
    new_backlinks = 0
    max_level = 0
    verify_budget = max_verify
    stop_reason = "converged"

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
        if targets_queried >= max_queries:
            stop_reason = "max-queries"
            break
        remaining = max_queries - targets_queried
        batch = list(
            connection.execute(
                "SELECT url FROM nodes WHERE level=? AND status='pending' "
                "ORDER BY first_seen, url LIMIT ?",
                (level, remaining),
            )
        )
        if not batch:
            continue
        max_level = max(max_level, level)
        bucket = levels.setdefault(
            level,
            {"targets": 0, "new": 0, "queries": 0, "errors": 0, "blocked": 0},
        )
        level_new = 0
        blocked = 0

        for item in batch:
            target = item["url"]
            targets_queried += 1
            bucket["targets"] += 1
            pending_providers = [
                name
                for name in selected
                if not _query_already_ran(connection, target, name)
            ]
            if not pending_providers:
                connection.execute(
                    "UPDATE nodes SET status='done', queried_at=? WHERE url=?",
                    (timestamp(), target),
                )
                connection.commit()
                continue
            progress(
                f"level {level}: querying {target} via "
                f"{', '.join(pending_providers)} ..."
            )
            with ThreadPoolExecutor(
                max_workers=min(options.workers, len(pending_providers))
            ) as pool:
                results = list(
                    pool.map(
                        lambda name: _run_provider(
                            name, registry[name], target, options
                        ),
                        pending_providers,
                    )
                )
            for result in results:
                bucket["queries"] += 1
                status = (
                    "error"
                    if result.error and not result.backlinks
                    else "ok"
                    if result.backlinks
                    else "empty"
                )
                _record_query(
                    connection,
                    target,
                    result.provider,
                    level,
                    status,
                    len(result.backlinks),
                    result.error,
                )
                if result.error:
                    bucket["errors"] += 1
                for backlink in result.backlinks:
                    candidate = normalize_url(backlink.url, backlink.url)
                    if not candidate:
                        continue
                    if not _acceptable(
                        candidate,
                        target,
                        include_internal=include_internal,
                        allow=allow,
                        deny=deny,
                    ):
                        continue
                    known = connection.execute(
                        "SELECT level FROM nodes WHERE url=?", (candidate,)
                    ).fetchone()
                    if level >= max_depth:
                        if known is None:
                            blocked += 1
                        continue
                    if node_count >= max_pages:
                        if known is None:
                            blocked += 1
                        continue

                    relation = backlink.relation
                    title = backlink.title
                    http_status = None
                    if verify and relation != "hyperlink":
                        if verify_budget > 0:
                            verify_budget -= 1
                            relation, page_title, http_status = classify_backlink(
                                candidate, target, options
                            )
                            title = page_title or title
                            if relation == "unverified" and not keep_unverified:
                                continue
                            if http_status:
                                connection.execute(
                                    "UPDATE nodes SET http_status=? WHERE url=?",
                                    (http_status, candidate),
                                )
                        elif not keep_unverified:
                            continue
                        else:
                            relation = "unverified"
                    inserted = _insert_node(
                        connection,
                        candidate,
                        level + 1,
                        target,
                        result.provider,
                        relation,
                        title,
                    )
                    _insert_edge(
                        connection,
                        candidate,
                        target,
                        int(known["level"]) if known is not None else level + 1,
                        result.provider,
                        relation,
                    )
                    if inserted:
                        node_count += 1
                        level_new += 1
                        new_backlinks += 1
            connection.execute(
                "UPDATE nodes SET status='done', queried_at=? WHERE url=?",
                (timestamp(), target),
            )
            connection.commit()

        bucket["new"] += level_new
        bucket["blocked"] += blocked
        connection.commit()

        if blocked and not level_new:
            stop_reason = "max-depth" if level >= max_depth else "max-pages"
            break
        if level_new == 0:
            deeper = connection.execute(
                "SELECT 1 FROM nodes WHERE status='pending' AND level > ? LIMIT 1",
                (level,),
            ).fetchone()
            if deeper is None:
                stop_reason = "converged"
                break
        if targets_queried >= max_queries:
            stop_reason = "max-queries"
            break
        if node_count >= max_pages:
            stop_reason = "max-pages"
            break

    set_meta(connection, "finished_at", timestamp())
    set_meta(connection, "stop_reason", stop_reason)
    set_meta(connection, "max_level", str(max_level))
    set_meta(connection, "targets_queried", str(targets_queried))
    set_meta(connection, "converged", "yes" if stop_reason == "converged" else "no")
    connection.commit()
    summary = {
        "seeds": len(seeds),
        "seed_added": seed_added,
        "seed_existing": seed_existing,
        "levels": [
            {
                "level": level,
                "seed_label": level_label(level, "seed"),
                "backlink_label": level_label(level, "backlink"),
                **values,
            }
            for level, values in sorted(levels.items())
        ],
        "max_level": max_level,
        "targets_queried": targets_queried,
        "new_backlinks": new_backlinks,
        "converged": stop_reason == "converged",
        "stop_reason": stop_reason,
        "providers": selected,
        "db": str(db_path),
    }
    connection.close()
    return summary


def print_summary(summary: dict) -> None:
    print(f"database: {summary['db']}")
    print(f"providers: {', '.join(summary['providers'])}")
    print(
        f"seeds: {summary['seeds']} "
        f"({summary['seed_added']} new, {summary['seed_existing']} already stored)"
    )
    for row in summary["levels"]:
        title = (
            row["seed_label"]
            if row["level"] == 1
            else f"{row['backlink_label']} / {row['seed_label']}"
        )
        print(
            f"level {row['level']} ({title}): "
            f"targets={row['targets']} new={row['new']} "
            f"queries={row['queries']} errors={row['errors']}"
        )
    print(
        f"stop: {summary['stop_reason']} after {summary['targets_queried']} "
        f"target(s), max level {summary['max_level']}"
    )
    print(f"new backlinks this run: {summary['new_backlinks']}")
    if summary["converged"]:
        print("every backlink found on the last level was already in the database")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Discover backlinks level by level: level 1 seeds, then pages that "
            "link to or mention them, then their backlinks, until a level adds "
            "no new URL."
        )
    )
    parser.add_argument("--seed", action="append", default=[], help="Seed URL (repeatable).")
    parser.add_argument("--seed-file", help="File with one seed URL per line (# comments allowed).")
    parser.add_argument(
        "--db",
        default="backlink-graph.sqlite3",
        help="SQLite database path (default: backlink-graph.sqlite3).",
    )
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDERS),
        help=f"Comma-separated providers, or 'all'. Available: {', '.join(PROVIDERS)}.",
    )
    parser.add_argument("--max-depth", type=int, default=2, help="Deepest level to query (default: 2).")
    parser.add_argument("--max-queries", type=int, default=20, help="Targets queried in this run (default: 20).")
    parser.add_argument("--max-pages", type=int, default=200, help="Maximum stored pages (default: 200).")
    parser.add_argument("--max-verify", type=int, default=40, help="Pages fetched to confirm candidates (default: 40).")
    parser.add_argument("--limit", type=int, default=20, help="Results requested per provider (default: 20).")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent providers per target (default: 4).")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between HTTP requests (default: 0.5).")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds (default: 20).")
    parser.add_argument("--max-bytes", type=int, default=2_000_000, help="Maximum bytes read per page.")
    parser.add_argument("--no-verify", dest="verify", action="store_false", help="Skip fetching candidates to confirm the link.")
    parser.add_argument("--keep-unverified", action="store_true", help="Keep candidates whose page could not be verified.")
    parser.add_argument("--include-internal", action="store_true", help="Also keep links on the target's own host.")
    parser.add_argument("--allow-regex", help="Only keep backlink URLs matching this regular expression.")
    parser.add_argument("--deny-regex", help="Drop backlink URLs matching this regular expression.")
    parser.add_argument("--refresh", action="store_true", help="Clear query history and re-run every level.")
    parser.add_argument("--searx-url", help="Optional SearXNG base URL with the JSON API enabled.")
    parser.add_argument("--lemmy-instance", default="https://lemmy.world", help="Lemmy instance for the lemmy provider.")
    parser.add_argument("--se-sites", default="stackoverflow", help="Comma-separated Stack Exchange sites.")
    parser.add_argument("--wikipedia-lang", default="en", help="Wikipedia language code (default: en).")
    parser.add_argument("--index-file", help="JSONL/CSV backlink index (source,target) for the index provider.")
    parser.add_argument("--export-nodes", help="Write nodes with level/label/role as JSON Lines.")
    parser.add_argument("--export-edges", help="Write source->target edges as JSON Lines.")
    parser.add_argument("--report-only", action="store_true", help="Print the stored graph summary and exit.")
    parser.add_argument("--json", action="store_true", help="Print the run summary as JSON.")
    args = parser.parse_args(argv)

    if args.report_only:
        print(json.dumps(report(args.db), indent=2, ensure_ascii=False))
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

    if args.providers.strip().lower() == "all":
        selected = list(DEFAULT_PROVIDERS)
        if args.searx_url:
            selected.append("searx")
        if args.index_file:
            selected.append("index")
    else:
        selected = [name.strip() for name in args.providers.split(",") if name.strip()]

    try:
        index = load_index(args.index_file) if args.index_file else {}
        options = ProviderOptions(
            fetcher=functools.partial(
                http_fetch, timeout=args.timeout, max_bytes=args.max_bytes
            ),
            limiter=RateLimiter(args.delay),
            limit=args.limit,
            workers=args.workers,
            searx_url=args.searx_url,
            lemmy_instance=args.lemmy_instance,
            se_sites=tuple(
                site.strip() for site in args.se_sites.split(",") if site.strip()
            ),
            wikipedia_lang=args.wikipedia_lang,
            index=index,
        )
        summary = crawl(
            seeds,
            args.db,
            providers=selected,
            options=options,
            verify=args.verify,
            keep_unverified=args.keep_unverified,
            include_internal=args.include_internal,
            max_depth=args.max_depth,
            max_queries=args.max_queries,
            max_pages=args.max_pages,
            max_verify=args.max_verify,
            allow_pattern=args.allow_regex,
            deny_pattern=args.deny_regex,
            refresh=args.refresh,
        )
    except (ValueError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.export_nodes:
        print(f"exported {export_nodes(args.db, args.export_nodes)} node(s)")
    if args.export_edges:
        print(f"exported {export_edges(args.db, args.export_edges)} edge(s)")
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
