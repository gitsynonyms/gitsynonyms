#!/usr/bin/env python3
"""Module 1 - 舆情监控级素材获取部 (public-opinion monitoring acquisition).

The acquisition department is the only layer that talks to the outside world on
behalf of the analysis modules. It turns a fixed source registry into a citable
material index:

    <out>/ledger.jsonl       append-only revisions, one JSON object per change
    <out>/manifest.json      materialised index consumed by the next module
    <out>/assets/            optional byte-for-byte copies named by checksum
    <out>/.http-cache.json   conditional-request cache (ETag / Last-Modified)
    <out>/.seen.json         last time each material was observed

Design rules
------------
* Every record names its source, URL, fetch time and content checksum, so any
  claim downstream can be traced back to what was actually observed.
* One record per event and kind. The same story seen in a second source is
  merged as corroboration instead of being stored twice; the merge keeps the
  richer capture and the full provenance history.
* Quality is graded, not assumed: ``A`` means the payload was captured and
  hashed, ``B`` means a citable reference, ``C`` means not enough for
  monitoring use (recorded for audit but not delivered).
* A dead source degrades to ``error``/``unavailable`` in the run report and
  never stops the other sources: a monitoring run must produce what it can.

Only the Python standard library is required. ``yt-dlp`` is imported lazily and
only for the YouTube sources.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "output" / "materials"
SCHEMA = "gitsynonyms.materials/1"
UA = "Mozilla/5.0 (compatible; gitsynonyms-acquisition/1.0)"

UTC = timezone.utc
DELIVERED_GRADES = ("A", "B")
TRUST_TIERS = {
    "official": 1.0,
    "wire": 0.8,
    "press": 0.7,
    "aggregator": 0.6,
    "social": 0.4,
    "unknown": 0.2,
}
MIN_TEXT_CHARS = {
    "article": 60,
    "page": 240,
    "transcript": 120,
    "caption": 20,
    "video": 0,
    "asset": 0,
}
CAPTURE_KINDS = ("article", "page", "transcript", "caption")
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "ref_url",
        "source",
        "_ga",
        "spm",
        "yclid",
    }
)


@dataclass(frozen=True)
class SourceSpec:
    """One place material can come from.

    ``kind`` selects the reader: ``rss`` (also Atom), ``json``, ``html``,
    ``youtube`` or ``asset``. ``parser`` refines ``json`` sources.
    """

    name: str
    kind: str
    url: str
    trust: str = "unknown"
    language: str = "en"
    enabled: bool = True
    parser: str = ""
    region: str = ""
    note: str = ""


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        name="whitehouse-news",
        kind="rss",
        url="https://www.whitehouse.gov/news/feed/",
        trust="official",
        note="Official White House news and remarks feed.",
    ),
    SourceSpec(
        name="whitehouse-actions",
        kind="rss",
        url="https://www.whitehouse.gov/presidential-actions/feed/",
        trust="official",
        note="Presidential actions, proclamations and executive orders.",
    ),
    SourceSpec(
        name="whitehouse-youtube",
        kind="youtube",
        url="https://www.youtube.com/@WhiteHouse/videos",
        trust="official",
        note="Video material; requires yt-dlp, degrades to unavailable without it.",
    ),
    SourceSpec(
        name="whitehouse-streams",
        kind="youtube",
        url="https://www.youtube.com/@WhiteHouse/streams",
        trust="official",
        note="Live and past streams of the same channel.",
    ),
    SourceSpec(
        name="factbase-calendar",
        kind="html",
        url="https://rollcall.com/factbase/trump/calendar/",
        trust="wire",
        region="calendar-results",
        note="Independent day-by-day schedule used to confirm events.",
    ),
    SourceSpec(
        name="gdelt-whitehouse",
        kind="json",
        url=(
            "https://api.gdeltproject.org/api/v2/doc/doc"
            "?query=%22white%20house%22&mode=ArtList&format=json"
            "&maxrecords=50&timespan=3d&sort=datedesc"
        ),
        trust="aggregator",
        parser="gdelt",
        note="Mainstream coverage across outlets, no API key required.",
    ),
)


# ---------------------------------------------------------------------------
# Small utilities: time, URLs, hashing
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """Render an aware datetime as ISO-8601 UTC with a trailing ``Z``."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_DURATION = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)


def parse_moment(value: str, *, now: datetime | None = None) -> datetime:
    """Parse ``24h``/``90m``/``7d`` or an ISO date/datetime into UTC."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty timestamp")
    match = _DURATION.match(raw)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        delta = {
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
        }[unit]
        return (now or utc_now()) - delta
    moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def parse_published(value: str) -> str:
    """Normalise the timestamp shapes feeds publish to ISO-8601 UTC, or ''."""
    raw = (value or "").strip()
    if not raw:
        return ""
    moment = None
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    if moment is None:
        for pattern in (
            "%Y%m%dT%H%M%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y%m%d",
        ):
            try:
                moment = datetime.strptime(raw, pattern).replace(tzinfo=UTC)
                break
            except ValueError:
                continue
    if moment is None:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return iso(moment)


def in_window(published: str, since: datetime | None, until: datetime | None) -> bool:
    """Undated entries are kept: a fresh feed entry is still material."""
    if not published:
        return True
    try:
        moment = parse_moment(published)
    except ValueError:
        return True
    if since is not None and moment < since:
        return False
    if until is not None and moment > until:
        return False
    return True


def age_hours(published: str, now: datetime) -> float | None:
    if not published:
        return None
    try:
        moment = parse_moment(published)
    except ValueError:
        return None
    return round((now - moment).total_seconds() / 3600.0, 2)


_YT_PATTERNS = (
    re.compile(r"[?&]v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"youtube\.com/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})"),
)
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(url: str) -> str:
    raw = (url or "").strip()
    for pattern in _YT_PATTERNS:
        match = pattern.search(raw)
        if match:
            return match.group(1)
    return raw if _YT_ID.match(raw) else ""


def canonical_url(url: str) -> str:
    """Drop tracking noise and normalise the parts used for identity."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urllib.parse.urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return ""
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    video_id = youtube_id(raw) if "youtu" in host else ""
    if host == "youtu.be" and video_id:
        return urllib.parse.urlunsplit(
            ("https", "youtube.com", "/watch", f"v={video_id}", "")
        )
    try:
        port = parts.port
    except ValueError:
        port = None
    default_port = {"http": 80, "https": 443}.get(scheme)
    netloc = host if port in (None, default_port) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    query.sort()
    return urllib.parse.urlunsplit((scheme, netloc, path, urllib.parse.urlencode(query), ""))


def event_key(url: str) -> str:
    """A YouTube video keeps one identity across every URL shape and source."""
    video_id = youtube_id(url)
    if video_id:
        return f"yt:{video_id}"
    canonical = canonical_url(url)
    return f"url:{canonical}" if canonical else ""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes((value or "").encode("utf-8"))


def material_id(kind: str, key: str) -> str:
    return hashlib.sha1(f"{key}|{kind}".encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Readable text extraction
# ---------------------------------------------------------------------------

_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "nav",
    "footer",
    "form",
    "aside",
    "iframe",
}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "header",
    "blockquote",
    "tr",
    "td",
    "table",
}
_BOILERPLATE = re.compile(
    r"The post .{0,200}? appeared first on .{0,200}?\.", re.IGNORECASE | re.DOTALL
)


_MAIN_TAGS = {"main", "article"}
_CONTENT_HINTS = (
    "entry-content",
    "article-content",
    "article-body",
    "story-body",
    "post-content",
)


class _TextExtractor(HTMLParser):
    """Collect visible text, the document title and the declared language.

    Text is accumulated three ways: the whole document, ``<main>``/``<article>``
    regions, and elements whose class/id matches a content hint. The caller
    picks the most specific region that is substantial.
    """

    def __init__(self, hints: Sequence[str] = ()) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.language = ""
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        self.regions: list[list[str]] = []
        self._hints = tuple(hint for hint in hints if hint) or _CONTENT_HINTS
        self._frames: list[dict] = []
        self._in_title = False
        self._skip = 0
        self._main_depth = 0

    def _matches(self, marker: str) -> bool:
        return any(hint in marker for hint in self._hints)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "html" and not self.language:
            self.language = dict(attrs).get("lang") or ""
        for frame in self._frames:
            frame["depth"] += 1
        marker = " ".join(
            value for key, value in attrs if key in ("class", "id") and value
        )
        if marker and self._matches(marker):
            self.regions.append([])
            self._frames.append(
                {"tag": tag, "depth": 1, "index": len(self.regions) - 1}
            )
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag in _MAIN_TAGS:
            self._main_depth += 1
        if tag == "title" and not self._skip and not self.title and not self._in_title:
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
            if self._main_depth:
                self.main_parts.append("\n")
            for frame in self._frames:
                self.regions[frame["index"]].append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in _MAIN_TAGS and self._main_depth:
            self._main_depth -= 1
        for frame in list(self._frames):
            if frame["tag"] != tag:
                continue
            frame["depth"] -= 1
            if frame["depth"] <= 0:
                self._frames.remove(frame)
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
            if self._main_depth:
                self.main_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if data.strip():
            self.parts.append(data)
            if self._main_depth:
                self.main_parts.append(data)
            for frame in self._frames:
                self.regions[frame["index"]].append(data)




_KNOWN_LANGUAGES = {"en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"}
_LANGUAGE_NAMES = {
    "english": "en",
    "chinese": "zh",
    "japanese": "ja",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "russian": "ru",
    "arabic": "ar",
}


def language_code(value: str) -> str:
    """Map a declared language name or tag to a short code, or ''."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if raw in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[raw]
    short = raw.split("-")[0]
    return short if short in _KNOWN_LANGUAGES else ""


def _clean_parts(parts: list[str]) -> str:
    lines = []
    for line in "".join(parts).splitlines():
        cleaned = re.sub(r"[ \t\u00a0]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return _BOILERPLATE.sub("", "\n".join(lines)).strip()


def extract_readable(source: str, hints: Sequence[str] = ()) -> dict:
    """Return ``title``, ``text``, ``language`` and ``text_chars`` for a page.

    The most specific substantial region wins: ``<main>``/``<article>`` when the
    page has one, otherwise a source-declared container, otherwise the whole
    document. Site navigation around the story therefore does not become
    material.
    """
    if not source:
        return {"title": "", "text": "", "language": "", "text_chars": 0}
    extractor = _TextExtractor(hints)
    try:
        extractor.feed(source)
        extractor.close()
    except Exception:  # noqa: BLE001 - malformed markup must not lose the text
        pass
    whole = _clean_parts(extractor.parts)
    main = _clean_parts(extractor.main_parts)
    best_region = ""
    for parts in extractor.regions:
        candidate = _clean_parts(parts)
        if len(candidate) > len(best_region):
            best_region = candidate
    if len(main) >= 200:
        text = main
    elif len(best_region) >= 200:
        text = best_region
    else:
        text = whole
    language = (extractor.language or "").split("-")[0].lower()
    return {
        "title": re.sub(r"\s+", " ", html.unescape(extractor.title)).strip(),
        "text": text,
        "language": language if language in _KNOWN_LANGUAGES else "",
        "text_chars": len(text),
    }


def html_to_text(source: str) -> str:
    """Reduce HTML (or a CDATA-wrapped summary) to plain text."""
    if not source:
        return ""
    if "<" in source:
        return extract_readable(source)["text"]
    return re.sub(r"\s+", " ", html.unescape(source)).strip()


# ---------------------------------------------------------------------------
# Feed and API readers
# ---------------------------------------------------------------------------


@dataclass
class FeedEntry:
    title: str = ""
    link: str = ""
    published: str = ""
    summary: str = ""
    language: str = ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _first_text(node: ET.Element, names: Sequence[str]) -> str:
    for child in node:
        if _local(child.tag) in names:
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def parse_feed(source: str) -> list[FeedEntry]:
    """Parse RSS 2.0, RSS 1.0 and Atom into the same entry shape."""
    root = ET.fromstring(source)
    entries: list[FeedEntry] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        links: list[tuple[str, str]] = []
        for child in node:
            if _local(child.tag) != "link":
                continue
            href = (child.get("href") or "").strip()
            if href:
                links.append(((child.get("rel") or "alternate").lower(), href))
            elif (child.text or "").strip():
                links.append(("alternate", child.text.strip()))
        link = next((href for rel, href in links if rel == "alternate"), "")
        if not link and links:
            link = links[0][1]
        if not link:
            guid = _first_text(node, ("guid", "id"))
            link = guid if guid.startswith("http") else ""
        summary_markup = _first_text(
            node, ("encoded", "content", "description", "summary")
        )
        language = (
            node.get("{http://www.w3.org/XML/1998/namespace}lang")
            or _first_text(node, ("language",))
        ).split("-")[0].lower()
        entries.append(
            FeedEntry(
                title=html_to_text(_first_text(node, ("title",))),
                link=link,
                published=parse_published(
                    _first_text(node, ("pubDate", "published", "updated", "date"))
                ),
                summary=html_to_text(summary_markup),
                language=language_code(language),
            )
        )
    return entries


def parse_gdelt(payload: dict) -> list[FeedEntry]:
    """GDELT's ArtList response: headline-level coverage records."""
    entries: list[FeedEntry] = []
    for article in payload.get("articles") or []:
        title = (article.get("title") or "").strip()
        url = (article.get("url") or "").strip()
        if not (title and url):
            continue
        domain = (article.get("domain") or "").strip()
        country = (article.get("sourcecountry") or "").strip()
        summary = " - ".join(part for part in (title, domain, country) if part)
        entries.append(
            FeedEntry(
                title=title,
                link=url,
                published=parse_published(article.get("seendate") or ""),
                summary=summary,
                language=language_code(article.get("language") or ""),
            )
        )
    return entries


def parse_json_items(payload: object) -> list[FeedEntry]:
    """A tolerant reader for simple ``{"items": [...]}`` or ``[...]`` APIs."""
    items: Iterable = ()
    if isinstance(payload, dict):
        for key in ("items", "results", "articles", "data"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    elif isinstance(payload, list):
        items = payload
    entries: list[FeedEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("headline") or "").strip()
        url = str(item.get("url") or item.get("link") or "").strip()
        if not (title and url):
            continue
        entries.append(
            FeedEntry(
                title=title,
                link=url,
                published=parse_published(
                    str(item.get("published") or item.get("date") or item.get("seendate") or "")
                ),
                summary=str(item.get("summary") or item.get("description") or title).strip(),
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Quality gates and the material record
# ---------------------------------------------------------------------------


def assess_quality(
    kind: str,
    *,
    source: str,
    url: str,
    fetched_at: str,
    published_at: str,
    language: str,
    sha256: str,
    byte_size: int,
    title: str,
    details: dict,
) -> dict:
    """Grade a record for monitoring use instead of trusting it blindly.

    ``A`` payload captured and hashed, ``B`` citable reference, ``C`` not
    enough to support a judgement.
    """
    text_chars = int(details.get("text_chars") or 0)
    capture = details.get("capture") or ""
    reference = kind in {"video", "asset"}
    checksum_ok = bool(re.fullmatch(r"[0-9a-f]{64}", sha256 or ""))
    payload_ok = capture in {"file", "page"} and byte_size > 0
    if reference:
        content_ok = bool(title and url and checksum_ok)
    else:
        content_ok = byte_size > 0 and text_chars >= MIN_TEXT_CHARS.get(kind, 80)
    checks = {
        "provenance": bool(source and url and fetched_at),
        "time_anchor": bool(published_at or fetched_at),
        "checksum": checksum_ok,
        "payload": payload_ok,
        "content": content_ok,
        "language": bool(language),
    }
    if all(checks.values()):
        grade = "A"
    elif (
        checks["provenance"]
        and checks["time_anchor"]
        and checks["checksum"]
        and (checks["content"] or checks["payload"])
    ):
        grade = "B"
    else:
        grade = "C"
    return {
        "grade": grade,
        "checks": checks,
        "reasons": [name for name, ok in checks.items() if not ok],
        "text_chars": text_chars,
        "capture": capture or "reference",
    }


@dataclass
class Material:
    """One citable piece of material, keyed by event and kind."""

    source: str
    kind: str
    url: str
    title: str = ""
    published_at: str = ""
    language: str = "en"
    mime: str = ""
    byte_size: int = 0
    sha256: str = ""
    trust: str = "unknown"
    trust_score: float = 0.0
    status: str = "acquired"
    fetched_at: str = ""
    canonical_url: str = ""
    event_key: str = ""
    material_id: str = ""
    grade: str = ""
    quality: dict = field(default_factory=dict)
    revision: int = 1
    first_seen_at: str = ""
    last_seen_at: str = ""
    local_path: str = ""
    details: dict = field(default_factory=dict)

    def finalize(self) -> "Material":
        key = event_key(self.url)
        self.canonical_url = canonical_url(self.url)
        self.event_key = key
        self.material_id = material_id(self.kind, key)
        self.trust_score = TRUST_TIERS.get(self.trust, TRUST_TIERS["unknown"])
        if not self.fetched_at:
            self.fetched_at = iso(utc_now())
        if not self.first_seen_at:
            self.first_seen_at = self.fetched_at
        self.quality = assess_quality(
            self.kind,
            source=self.source,
            url=self.url,
            fetched_at=self.fetched_at,
            published_at=self.published_at,
            language=self.language,
            sha256=self.sha256,
            byte_size=self.byte_size,
            title=self.title,
            details=self.details,
        )
        self.grade = self.quality["grade"]
        if self.grade == "C" and self.status == "acquired":
            self.status = "rejected"
        return self

    def to_json(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# HTTP with retries, byte caps, conditional requests and proxy rotation
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    status: str = "error"
    url: str = ""
    final_url: str = ""
    http_status: int = 0
    body: bytes = b""
    mime: str = ""
    etag: str = ""
    last_modified: str = ""
    fetched_at: str = ""
    sha256: str = ""
    error: str = ""


class _ResponseTooLarge(Exception):
    pass


class HttpFetcher:
    """Fetch bytes with a bounded size, retries and optional proxy rotation."""

    def __init__(
        self,
        *,
        timeout: float = 20,
        retries: int = 2,
        max_bytes: int = 8_000_000,
        user_agent: str = UA,
        proxies: Sequence[str] = (),
        backoff: float = 0.5,
        sleep=time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.max_bytes = max_bytes
        self.user_agent = user_agent
        self.proxies = tuple(proxies)
        self.backoff = backoff
        self._sleep = sleep
        self._cursor = 0

    def _next_proxy(self) -> str:
        if not self.proxies:
            return ""
        proxy = self.proxies[self._cursor % len(self.proxies)]
        self._cursor += 1
        return proxy

    def _opener(self, proxy: str):
        handler = urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy} if proxy else {}
        )
        return urllib.request.build_opener(handler)

    def get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        cache_entry: dict | None = None,
    ) -> FetchResult:
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            **(headers or {}),
        }
        if cache_entry:
            if cache_entry.get("etag"):
                request_headers["If-None-Match"] = cache_entry["etag"]
            if cache_entry.get("last_modified"):
                request_headers["If-Modified-Since"] = cache_entry["last_modified"]
        attempts = self.retries + 1
        last = FetchResult(url=url, error="not attempted")
        for attempt in range(attempts):
            proxy = self._next_proxy()
            try:
                request = urllib.request.Request(url, headers=request_headers)
                with self._opener(proxy).open(request, timeout=self.timeout) as response:
                    body = self._read_bounded(response)
                    return FetchResult(
                        status="ok",
                        url=url,
                        final_url=response.geturl() or url,
                        http_status=int(getattr(response, "status", 0) or 0),
                        body=body,
                        mime=(response.headers.get("Content-Type") or "").split(";")[0].strip().lower(),
                        etag=response.headers.get("ETag") or "",
                        last_modified=response.headers.get("Last-Modified") or "",
                        fetched_at=iso(utc_now()),
                        sha256=sha256_bytes(body),
                    )
            except urllib.error.HTTPError as error:
                if error.code == 304:
                    return FetchResult(
                        status="not_modified",
                        url=url,
                        http_status=304,
                        fetched_at=iso(utc_now()),
                    )
                last = FetchResult(
                    url=url,
                    http_status=error.code,
                    error=f"HTTP {error.code} {error.reason}".strip(),
                )
                if error.code not in RETRY_STATUS:
                    return last
            except _ResponseTooLarge:
                return FetchResult(
                    url=url,
                    error=f"response larger than {self.max_bytes} bytes",
                )
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last = FetchResult(url=url, error=str(getattr(error, "reason", error)))
            if attempt + 1 < attempts:
                self._sleep(self.backoff * (2**attempt))
        return last

    def _read_bounded(self, response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > self.max_bytes:
                raise _ResponseTooLarge()
            chunks.append(chunk)
        return b"".join(chunks)


# ---------------------------------------------------------------------------
# State files
# ---------------------------------------------------------------------------


def load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_ledger(path: Path) -> dict[str, dict]:
    """Latest revision per material id; a torn last line is ignored."""
    records: dict[str, dict] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        identifier = record.get("material_id") if isinstance(record, dict) else ""
        if not identifier:
            continue
        previous = records.get(identifier)
        if previous is None or int(record.get("revision") or 0) >= int(
            previous.get("revision") or 0
        ):
            records[identifier] = record
    return records


def load_proxies(path: Path | None = None) -> tuple[str, ...]:
    pool = Path(path) if path else ROOT / "proxyindex" / "alive_http.txt"
    try:
        lines = pool.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    proxies = []
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        proxies.append(entry if "://" in entry else f"http://{entry}")
        if len(proxies) >= 200:
            break
    return tuple(proxies)


def as_int(value: object) -> int:
    """Tolerate ``"620"`` and ``620.0`` from downloader metadata."""
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def counter(values: Iterable) -> dict[str, int]:
    counted: dict[str, int] = {}
    for value in values:
        key = str(value if value is not None else "unknown")
        counted[key] = counted.get(key, 0) + 1
    return dict(sorted(counted.items()))


# ---------------------------------------------------------------------------
# The acquisition run
# ---------------------------------------------------------------------------


class SourceUnavailable(RuntimeError):
    """The source could not be read this run; other sources keep going."""


@dataclass
class SourceRun:
    materials: list[Material] = field(default_factory=list)
    status: str = "ok"
    note: str = ""


_GRADE_RANK = {"A": 3, "B": 2, "C": 1}
PROMOTED_FIELDS = (
    "title",
    "published_at",
    "language",
    "mime",
    "byte_size",
    "sha256",
    "url",
    "canonical_url",
    "event_key",
    "trust",
    "trust_score",
    "quality",
    "grade",
    "local_path",
)


def _rank(record: dict) -> tuple:
    quality = record.get("quality") or {}
    checks = quality.get("checks") or {}
    return (
        _GRADE_RANK.get(record.get("grade") or "C", 0),
        bool(checks.get("payload")),
        int(quality.get("text_chars") or 0),
        float(record.get("trust_score") or 0.0),
    )


def build_events(records: Sequence[dict]) -> list[dict]:
    """Group delivered material by event and record independent corroboration."""
    groups: dict[str, dict] = {}
    for record in records:
        key = record.get("event_key") or record.get("material_id") or ""
        group = groups.setdefault(
            key,
            {
                "trusts": {},
                "titles": [],
                "kinds": set(),
                "published": "",
                "materials": [],
            },
        )
        provenance = [
            (record.get("source") or "", float(record.get("trust_score") or 0.0))
        ]
        for alternate in (record.get("details") or {}).get("also_seen_in") or []:
            provenance.append(
                (
                    alternate.get("source") or "",
                    TRUST_TIERS.get(
                        alternate.get("trust") or "unknown", TRUST_TIERS["unknown"]
                    ),
                )
            )
        for source_name, trust in provenance:
            if source_name:
                group["trusts"].setdefault(source_name, trust)
        if record.get("title"):
            group["titles"].append(
                (
                    float(record.get("trust_score") or 0.0),
                    len(record["title"]),
                    record["title"],
                )
            )
        group["kinds"].add(record.get("kind") or "")
        published = record.get("published_at") or ""
        if published and (not group["published"] or published < group["published"]):
            group["published"] = published
        group["materials"].append(record.get("material_id") or "")
    events = []
    for key, group in groups.items():
        title = max(group["titles"])[2] if group["titles"] else ""
        trusts = group["trusts"]
        events.append(
            {
                "event_key": key,
                "title": title,
                "published_at": group["published"],
                "sources": sorted(trusts),
                "kinds": sorted(kind for kind in group["kinds"] if kind),
                "corroboration": len(trusts),
                "corroborated": len(trusts) > 1,
                "trust_score": round(min(1.0, sum(trusts.values())), 2),
                "materials": sorted(identifier for identifier in group["materials"] if identifier),
            }
        )
    events.sort(key=lambda event: (event["published_at"] or "", event["event_key"]), reverse=True)
    return events


class Acquisition:
    """One acquisition run over the enabled sources."""

    def __init__(
        self,
        out_dir: Path | str = DEFAULT_OUT,
        *,
        sources: Sequence[SourceSpec] | None = None,
        fetcher: HttpFetcher | None = None,
        now: datetime | None = None,
        youtube_lister=None,
        download: bool = False,
        fetch_articles: bool = False,
        sleep=time.sleep,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.sources = tuple(sources if sources is not None else SOURCES)
        self.now = now or utc_now()
        self.now_iso = iso(self.now)
        self.download = download
        self.fetch_articles = fetch_articles
        self.youtube_lister = youtube_lister or list_youtube
        self.sleep = sleep
        self.ledger_path = self.out_dir / "ledger.jsonl"
        self.manifest_path = self.out_dir / "manifest.json"
        self.cache_path = self.out_dir / ".http-cache.json"
        self.seen_path = self.out_dir / ".seen.json"
        self.asset_dir = self.out_dir / "assets"
        self.records = load_ledger(self.ledger_path)
        self.cache = load_json(self.cache_path, {})
        self.seen = load_json(self.seen_path, {})
        self.fetcher = fetcher or HttpFetcher(sleep=sleep)
        self._pending: list[dict] = []

    # -- bookkeeping --------------------------------------------------------

    def _append(self, record: dict) -> None:
        self._pending.append(
            {
                key: value
                for key, value in record.items()
                if not key.startswith("_") and key != "age_hours"
            }
        )

    def _flush(self) -> None:
        if not self._pending:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            for record in self._pending:
                handle.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                )
        self._pending.clear()

    @staticmethod
    def _base_report(spec: SourceSpec) -> dict:
        return {
            "name": spec.name,
            "kind": spec.kind,
            "trust": spec.trust,
            "url": spec.url,
            "status": "ok",
            "items": 0,
            "new": 0,
            "updated": 0,
            "unchanged": 0,
            "note": "",
        }

    # -- run ----------------------------------------------------------------

    def collect(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 20,
        source_names: Sequence[str] = (),
        dry_run: bool = False,
    ) -> dict:
        """Read every enabled source and materialise the manifest."""
        self.dry_run = dry_run
        wanted = [name for name in source_names if name]
        specs = [spec for spec in self.sources if not wanted or spec.name in wanted]
        reports: list[dict] = []
        counts = {"materials": 0, "new": 0, "updated": 0, "unchanged": 0, "rejected": 0}
        for spec in specs:
            report = self._base_report(spec)
            if not spec.enabled:
                report["status"] = "disabled"
                report["note"] = "source is disabled in the registry"
                reports.append(report)
                continue
            try:
                run = self._collect_source(spec, since, until, limit)
            except SourceUnavailable as error:
                report["status"] = "unavailable"
                report["note"] = str(error)
                reports.append(report)
                continue
            except Exception as error:  # noqa: BLE001 - one bad source must not stop the run
                report["status"] = "error"
                report["note"] = f"{type(error).__name__}: {error}"
                reports.append(report)
                continue
            for material in run.materials:
                record, change = self._ingest(material)
                report[change] += 1
                if change == "new" and record.get("grade") == "C":
                    counts["rejected"] += 1
            report["status"] = run.status
            report["items"] = len(run.materials)
            report["note"] = run.note
            counts["materials"] += len(run.materials)
            for key in ("new", "updated", "unchanged"):
                counts[key] += report[key]
            reports.append(report)
        unknown = sorted(set(wanted) - {spec.name for spec in self.sources})
        run_summary = {
            "window": {
                "since": iso(since) if since else None,
                "until": iso(until) if until else None,
                "limit": limit,
            },
            "sources": reports,
            "unknown_sources": unknown,
            "counts": counts,
            "dry_run": dry_run,
        }
        if not dry_run:
            self._flush()
            write_json(self.cache_path, self.cache)
            write_json(self.seen_path, self.seen)
        return self.finish(run_summary)

    # -- source readers -----------------------------------------------------

    def _collect_source(
        self,
        spec: SourceSpec,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> SourceRun:
        handlers = {
            "rss": self._collect_feed,
            "json": self._collect_json_source,
            "html": self._collect_page,
            "youtube": self._collect_youtube,
            "asset": self._collect_asset,
        }
        handler = handlers.get(spec.kind)
        if handler is None:
            raise SourceUnavailable(f"unknown source kind: {spec.kind}")
        return handler(spec, since, until, limit)

    def _fetch(self, url: str, *, cache_key: str = "", headers: dict | None = None) -> FetchResult:
        entry = self.cache.get(cache_key) if cache_key else None
        result = self.fetcher.get(url, headers=headers, cache_entry=entry)
        if result.status == "ok" and cache_key:
            self.cache[cache_key] = {
                "etag": result.etag,
                "last_modified": result.last_modified,
                "sha256": result.sha256,
                "fetched_at": result.fetched_at,
            }
        return result

    @staticmethod
    def _http_note(result: FetchResult) -> str:
        if result.error:
            return result.error
        return f"HTTP {result.http_status}" if result.http_status else "network error"

    def _collect_feed(
        self,
        spec: SourceSpec,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> SourceRun:
        result = self._fetch(
            spec.url,
            cache_key=spec.name,
            headers={"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"},
        )
        if result.status == "not_modified":
            return SourceRun(status="unchanged", note="feed not modified since the last run")
        if result.status != "ok":
            raise SourceUnavailable(self._http_note(result))
        try:
            entries = parse_feed(result.body.decode("utf-8", "replace"))
        except ET.ParseError as error:
            raise SourceUnavailable(f"feed is not valid XML: {error}") from error
        materials: list[Material] = []
        for entry in entries[:limit]:
            if not entry.link or not in_window(entry.published, since, until):
                continue
            material = Material(
                source=spec.name,
                kind="article",
                url=entry.link,
                title=entry.title,
                published_at=entry.published,
                language=entry.language or spec.language,
                mime="text/html",
                trust=spec.trust,
                details={
                    "capture": "entry",
                    "feed": spec.url,
                    "summary": entry.summary[:4000],
                    "text_chars": len(entry.summary),
                    "in_window": True,
                },
            )
            material.byte_size = len(entry.summary.encode("utf-8"))
            material.sha256 = sha256_text(
                "\n".join((entry.title, entry.link, entry.published, entry.summary))
            )
            if self.fetch_articles:
                try:
                    self._capture_page(
                        material, entry.link, (spec.region,) if spec.region else ()
                    )
                except SourceUnavailable as error:
                    material.details["page_error"] = str(error)
            materials.append(material)
        return SourceRun(
            materials=materials,
            note=f"{len(entries)} entries, {len(materials)} inside the window",
        )

    def _capture_page(
        self, material: Material, url: str, hints: Sequence[str] = ()
    ) -> None:
        """Upgrade an entry to a full-text capture; A grade needs the body."""
        result = self._fetch(url, headers={"Accept": "text/html, application/xhtml+xml"})
        if result.status != "ok":
            raise SourceUnavailable(self._http_note(result))
        page = extract_readable(result.body.decode("utf-8", "replace"), hints)
        if page["text_chars"] < 40:
            raise SourceUnavailable("page has no readable text")
        material.details.update(
            {
                "capture": "page",
                "text": page["text"][:20000],
                "text_chars": page["text_chars"],
                "page_title": page["title"],
            }
        )
        material.mime = result.mime or "text/html"
        material.byte_size = len(result.body)
        material.sha256 = sha256_text(page["text"])
        material.details["raw_sha256"] = result.sha256
        if not material.title:
            material.title = page["title"]
        if page["language"]:
            material.language = page["language"]

    def _collect_page(
        self,
        spec: SourceSpec,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> SourceRun:
        result = self._fetch(
            spec.url, cache_key=spec.name, headers={"Accept": "text/html, application/xhtml+xml"}
        )
        if result.status == "not_modified":
            return SourceRun(status="unchanged", note="page not modified since the last run")
        if result.status != "ok":
            raise SourceUnavailable(self._http_note(result))
        page = extract_readable(
            result.body.decode("utf-8", "replace"),
            (spec.region,) if spec.region else (),
        )
        if page["text_chars"] < 40:
            return SourceRun(status="ok", note="page has no readable text")
        material = Material(
            source=spec.name,
            kind="page",
            url=spec.url,
            title=page["title"] or spec.name,
            language=page["language"] or spec.language,
            mime=result.mime or "text/html",
            byte_size=len(result.body),
            trust=spec.trust,
            details={
                "capture": "page",
                "text": page["text"][:20000],
                "text_chars": page["text_chars"],
                "page_title": page["title"],
                "raw_sha256": result.sha256,
            },
        )
        # Content-addressed by readable text, so rotating markup is not a change.
        material.sha256 = sha256_text(page["text"])
        return SourceRun(
            materials=[material], note=f"{page['text_chars']} readable characters"
        )

    def _collect_json_source(
        self,
        spec: SourceSpec,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> SourceRun:
        result = self._fetch(
            spec.url, cache_key=spec.name, headers={"Accept": "application/json"}
        )
        if result.status == "not_modified":
            return SourceRun(status="unchanged", note="API answer not modified since the last run")
        if result.status != "ok":
            raise SourceUnavailable(self._http_note(result))
        try:
            payload = json.loads(result.body.decode("utf-8", "replace"))
        except json.JSONDecodeError as error:
            raise SourceUnavailable(f"source did not return JSON: {error}") from error
        entries = (
            parse_gdelt(payload)
            if spec.parser == "gdelt"
            else parse_json_items(payload)
        )
        materials: list[Material] = []
        for entry in entries[:limit]:
            if not entry.link or not in_window(entry.published, since, until):
                continue
            material = Material(
                source=spec.name,
                kind="article",
                url=entry.link,
                title=entry.title,
                published_at=entry.published,
                language=entry.language or spec.language,
                mime="application/json",
                trust=spec.trust,
                details={
                    "capture": "entry",
                    "api": spec.url,
                    "summary": entry.summary[:4000],
                    "text_chars": len(entry.summary),
                },
            )
            material.byte_size = len(entry.summary.encode("utf-8"))
            material.sha256 = sha256_text(
                "\n".join((entry.title, entry.link, entry.published, entry.summary))
            )
            materials.append(material)
        return SourceRun(
            materials=materials,
            note=f"{len(entries)} records, {len(materials)} inside the window",
        )

    def _collect_youtube(
        self,
        spec: SourceSpec,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> SourceRun:
        try:
            entries = list(self.youtube_lister(spec, limit))
        except SourceUnavailable:
            raise
        except Exception as error:  # noqa: BLE001 - yt-dlp raises many shapes
            raise SourceUnavailable(f"{type(error).__name__}: {error}") from error
        materials: list[Material] = []
        skipped_live = 0
        for entry in entries[:limit]:
            if not isinstance(entry, dict):
                continue
            video_id = str(entry.get("id") or youtube_id(str(entry.get("url") or "")))
            if not video_id:
                continue
            if entry.get("live_status") in {"is_live", "is_upcoming"}:
                skipped_live += 1
                continue
            published = parse_published(
                str(
                    entry.get("upload_date")
                    or entry.get("published")
                    or entry.get("timestamp")
                    or ""
                )
            )
            if not in_window(published, since, until):
                continue
            title = str(entry.get("title") or "").strip()
            material = Material(
                source=spec.name,
                kind="video",
                url=f"https://www.youtube.com/watch?v={video_id}",
                title=title,
                published_at=published,
                language=spec.language,
                mime="video/youtube",
                trust=spec.trust,
                details={
                    "capture": "reference",
                    "channel": spec.url,
                    "duration": as_int(entry.get("duration")),
                    "live_status": entry.get("live_status") or "",
                    "text_chars": len(title),
                },
            )
            material.sha256 = sha256_text(f"{video_id}|{title}|{published}")
            materials.append(material)
        note = f"{len(entries)} channel entries, {len(materials)} usable"
        if skipped_live:
            note += f", {skipped_live} live/upcoming skipped"
        return SourceRun(materials=materials, note=note)

    def _collect_asset(
        self,
        spec: SourceSpec,
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> SourceRun:
        result = self._fetch(spec.url, cache_key=spec.name)
        if result.status == "not_modified":
            return SourceRun(status="unchanged", note="asset not modified since the last run")
        if result.status != "ok":
            raise SourceUnavailable(self._http_note(result))
        suffix = Path(urllib.parse.urlsplit(spec.url).path).suffix
        suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix) else ""
        local_path = ""
        if self.download and not getattr(self, "dry_run", False):
            self.asset_dir.mkdir(parents=True, exist_ok=True)
            target = self.asset_dir / f"{result.sha256[:16]}{suffix}"
            target.write_bytes(result.body)
            local_path = str(target.relative_to(self.out_dir))
        material = Material(
            source=spec.name,
            kind="asset",
            url=spec.url,
            title=spec.name,
            language=spec.language,
            mime=result.mime,
            byte_size=len(result.body),
            sha256=result.sha256,
            trust=spec.trust,
            local_path=local_path,
            details={
                "capture": "file" if local_path else "remote",
                "content_type": result.mime,
                "text_chars": 0,
            },
        )
        return SourceRun(materials=[material], note=f"{len(result.body)} bytes")

    # -- merging and manifest ----------------------------------------------

    def _ingest(self, material: Material) -> tuple[dict, str]:
        """Merge one observation into the ledger; never overwrite history."""
        material.finalize()
        record = material.to_json()
        record["last_seen_at"] = self.now_iso
        record["first_seen_at"] = record.get("first_seen_at") or self.now_iso
        identifier = record["material_id"]
        existing = self.records.get(identifier)
        if existing is None:
            self.records[identifier] = record
            self.seen[identifier] = self.now_iso
            self._append(record)
            return record, "new"
        history = list((existing.get("details") or {}).get("also_seen_in") or [])
        history_sources = {item.get("source") for item in history}
        provenance_added = False
        if record["source"] != existing.get("source") and record["source"] not in history_sources:
            history.append(
                {
                    "source": record["source"],
                    "trust": record["trust"],
                    "url": record["url"],
                    "fetched_at": record["fetched_at"],
                }
            )
            provenance_added = True
        incoming_rank = _rank(record)
        existing_rank = _rank(existing)
        promote = incoming_rank > existing_rank or (
            record.get("sha256") != existing.get("sha256")
            and incoming_rank >= existing_rank
        )
        existing["last_seen_at"] = self.now_iso
        self.seen[identifier] = self.now_iso
        if promote:
            details = {**(existing.get("details") or {}), **(record.get("details") or {})}
            details["also_seen_in"] = history
            first_seen = existing.get("first_seen_at") or record["fetched_at"]
            for field_name in PROMOTED_FIELDS:
                existing[field_name] = record[field_name]
            existing["details"] = details
            existing["revision"] = int(existing.get("revision") or 1) + 1
            existing["first_seen_at"] = first_seen
            if existing.get("grade") in DELIVERED_GRADES and existing.get(
                "status"
            ) == "rejected":
                existing["status"] = "acquired"
            self._append(existing)
            return existing, "updated"
        if provenance_added:
            existing.setdefault("details", {})["also_seen_in"] = history
            self._append(existing)
            return existing, "updated"
        return existing, "unchanged"

    def finish(self, run: dict) -> dict:
        """Write the manifest that the next module reads."""
        records = sorted(
            self.records.values(),
            key=lambda item: (
                item.get("event_key") or "",
                item.get("kind") or "",
                item.get("source") or "",
            ),
        )
        for record in records:
            record["age_hours"] = age_hours(record.get("published_at") or "", self.now)
        delivered = [
            record
            for record in records
            if record.get("grade") in DELIVERED_GRADES
            and record.get("status") in ("acquired", "unchanged")
        ]
        events = build_events(delivered)
        manifest = {
            "schema": SCHEMA,
            "generated_at": self.now_iso,
            "window": run["window"],
            "counts": {
                "materials": len(records),
                "delivered": len(delivered),
                "rejected": sum(
                    1 for record in records if record.get("status") == "rejected"
                ),
                "events": len(events),
                "corroborated_events": sum(
                    1 for event in events if event["corroborated"]
                ),
                "by_grade": counter(record.get("grade") for record in records),
                "by_kind": counter(record.get("kind") for record in records),
                "by_source": counter(record.get("source") for record in records),
                "this_run": run["counts"],
            },
            "sources": run["sources"],
            "unknown_sources": run.get("unknown_sources") or [],
            "events": events,
            "materials": records,
        }
        if not run.get("dry_run"):
            write_json(self.manifest_path, manifest)
        return manifest


def list_youtube(spec: SourceSpec, limit: int) -> list[dict]:
    """List channel entries with yt-dlp; a missing yt-dlp is a source outage."""
    try:
        import yt_dlp
    except ImportError as error:
        raise SourceUnavailable("yt-dlp is not installed") from error
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "playlistend": limit,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        try:
            info = downloader.extract_info(spec.url, download=False)
        except Exception as error:  # noqa: BLE001
            raise SourceUnavailable(f"yt-dlp failed: {error}") from error
    entries = (info or {}).get("entries") or []
    return [entry for entry in entries if isinstance(entry, dict)]


# ---------------------------------------------------------------------------
# Reporting and CLI
# ---------------------------------------------------------------------------


def print_manifest_summary(manifest: dict, *, limit: int = 10, stream=None) -> None:
    out = stream or sys.stdout
    counts = manifest.get("counts") or {}
    window = manifest.get("window") or {}
    print(
        f"schema={manifest.get('schema')} generated={manifest.get('generated_at')}", file=out
    )
    print(
        f"window since={window.get('since')} until={window.get('until')} "
        f"limit={window.get('limit')}",
        file=out,
    )
    print(
        f"materials={counts.get('materials')} delivered={counts.get('delivered')} "
        f"rejected={counts.get('rejected')} events={counts.get('events')} "
        f"corroborated={counts.get('corroborated_events')}",
        file=out,
    )
    grades = counts.get("by_grade") or {}
    print("grades " + " ".join(f"{grade}={total}" for grade, total in grades.items()), file=out)
    for source in manifest.get("sources") or []:
        print(
            f"  {source.get('name', '?'):<22} {source.get('status', '?'):<12} "
            f"items={source.get('items', 0):<4} new={source.get('new', 0)} "
            f"updated={source.get('updated', 0)} unchanged={source.get('unchanged', 0)} "
            f"{source.get('note', '')}",
            file=out,
        )
    events = manifest.get("events") or []
    print(f"events (newest {min(limit, len(events))} of {len(events)}):", file=out)
    for event in events[:limit]:
        flag = "corroborated" if event.get("corroborated") else "single-source"
        print(
            f"  {event.get('published_at') or 'undated':<20} {flag:<13} "
            f"{event.get('title', '')[:70]}",
            file=out,
        )
    rejected = [
        record
        for record in manifest.get("materials") or []
        if record.get("status") == "rejected"
    ]
    if rejected:
        print(f"rejected {len(rejected)} records, first reasons:", file=out)
        for record in rejected[:limit]:
            reasons = ",".join((record.get("quality") or {}).get("reasons") or [])
            print(
                f"  {record.get('source')}: {record.get('title', '')[:60]} -> {reasons}",
                file=out,
            )


def report(
    out_dir: Path | str = DEFAULT_OUT, *, limit: int = 10, quiet: bool = False
) -> dict:
    manifest = load_json(Path(out_dir) / "manifest.json", None)
    if not isinstance(manifest, dict):
        raise FileNotFoundError(f"no manifest in {out_dir}; run collect first")
    if not quiet:
        print_manifest_summary(manifest, limit=limit)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acquisition",
        description=(
            "Module 1 - 舆情监控级素材获取部 "
            "(public-opinion monitoring material acquisition)"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="read every enabled source into the ledger")
    collect.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    collect.add_argument("--sources", default="", help="comma separated source names")
    collect.add_argument("--since", default="24h", help="ISO timestamp or 90m/24h/7d")
    collect.add_argument("--until", default="", help="ISO timestamp or relative window end")
    collect.add_argument("--limit", type=int, default=20, help="items per source")
    collect.add_argument(
        "--fetch-articles",
        action="store_true",
        help="also download each entry's page for a full-text capture",
    )
    collect.add_argument(
        "--download", action="store_true", help="store asset bytes under <out>/assets"
    )
    collect.add_argument("--proxy-pool", default="", help="proxy list file to rotate")
    collect.add_argument("--timeout", type=float, default=20, help="per-request timeout")
    collect.add_argument("--retries", type=int, default=2, help="retries per request")
    collect.add_argument("--dry-run", action="store_true", help="do not write any file")
    collect.add_argument("--json", action="store_true", dest="as_json")
    collect.add_argument("--quiet", action="store_true")

    listing = sub.add_parser("sources", help="show the source registry")
    listing.add_argument("--json", action="store_true", dest="as_json")

    status = sub.add_parser("report", help="summarise the last manifest")
    status.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    status.add_argument("--limit", type=int, default=10, help="events to show")
    status.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "sources":
        rows = [asdict(spec) for spec in SOURCES]
        if args.as_json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for spec in SOURCES:
                print(f"{spec.name:<22} {spec.kind:<8} {spec.trust:<10} {spec.url}")
        return 0
    if args.command == "report":
        try:
            manifest = report(args.out, limit=args.limit, quiet=args.as_json)
        except FileNotFoundError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.as_json:
            keys = ("schema", "generated_at", "window", "counts", "sources", "events")
            print(
                json.dumps(
                    {key: manifest.get(key) for key in keys}, indent=2, ensure_ascii=False
                )
            )
        return 0

    now = utc_now()
    try:
        since = parse_moment(args.since, now=now) if args.since else None
        until = parse_moment(args.until, now=now) if args.until else None
    except ValueError as error:
        print(f"error: invalid window: {error}", file=sys.stderr)
        return 2
    proxies = load_proxies(Path(args.proxy_pool)) if args.proxy_pool else ()
    fetcher = HttpFetcher(timeout=args.timeout, retries=args.retries, proxies=proxies)
    requested = [name.strip() for name in args.sources.split(",") if name.strip()]
    known = {spec.name for spec in SOURCES}
    unknown = sorted(set(requested) - known)
    if unknown:
        print(
            f"error: unknown source(s): {', '.join(unknown)}; "
            f"known: {', '.join(sorted(known))}",
            file=sys.stderr,
        )
        return 2
    run = Acquisition(
        args.out,
        fetcher=fetcher,
        now=now,
        download=args.download,
        fetch_articles=args.fetch_articles,
    )
    manifest = run.collect(
        since=since,
        until=until,
        limit=args.limit,
        source_names=requested,
        dry_run=args.dry_run,
    )
    if args.as_json:
        keys = ("schema", "generated_at", "window", "counts", "sources", "events")
        print(
            json.dumps(
                {key: manifest.get(key) for key in keys}, indent=2, ensure_ascii=False
            )
        )
    elif not args.quiet:
        print_manifest_summary(manifest, limit=args.limit)
    statuses = [source.get("status") for source in manifest.get("sources") or []]
    if statuses and all(status in {"error", "unavailable"} for status in statuses):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())












