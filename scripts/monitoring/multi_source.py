#!/usr/bin/env python3
"""Multi-source discovery and verification for White House event videos.

Discovery: YouTube channels (videos + streams), YouTube Data API, Google CSE,
C-SPAN and the RollCall Factbase calendar, so one event can be confirmed by
several independent sources before anything is downloaded.

Verification: GDELT, NewsBank, congressional records, YouTube comments,
forums and reverse links, so a candidate is corroborated rather than trusted
on its own.

Every source degrades gracefully: a missing API key only disables that source.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

UA = "Mozilla/5.0 (X11; Linux x86_64) gitsynonyms-monitor/1.0"

YOUTUBE_CHANNELS = {
    "TheWhiteHouse/videos": "https://www.youtube.com/@WhiteHouse/videos",
    "TheWhiteHouse/streams": "https://www.youtube.com/@WhiteHouse/streams",
}
FACTSBASE_CALENDAR = "https://rollcall.com/factbase/trump/calendar/"
CSPAN_SEARCH = "https://www.c-span.org/search/?query={query}&sort=Newest"


@dataclass
class Candidate:
    """One event candidate found by any source."""

    video_id: str = ""
    title: str = ""
    source: str = ""
    url: str = ""
    published: str = ""
    duration: int = 0
    event_date: str = ""
    confidence: float = 0.0
    details: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


def http_get(url: str, *, timeout: int = 20, headers: dict | None = None) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": UA, **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def http_get_json(url: str, *, timeout: int = 20, headers: dict | None = None) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def discover_youtube_channel(channel_url: str, *, limit: int = 25) -> list[Candidate]:
    """List recent uploads of a YouTube channel with yt-dlp."""
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "playlistend": limit,
        "ignoreerrors": True,
    }
    candidates: list[Candidate] = []
    with yt_dlp.YoutubeDL(options) as ydl:
        try:
            info = ydl.extract_info(channel_url, download=False)
        except Exception as error:  # noqa: BLE001 - a dead source must not stop the run
            print(f"  youtube source failed {channel_url}: {error}", flush=True)
            return candidates
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        video_id = entry.get("id") or ""
        if not video_id:
            continue
        candidates.append(
            Candidate(
                video_id=video_id,
                title=entry.get("title") or "",
                source="youtube",
                url=f"https://www.youtube.com/watch?v={video_id}",
                published=str(entry.get("upload_date") or entry.get("timestamp") or ""),
                duration=int(entry.get("duration") or 0),
                confidence=0.9,
                details={"channel": channel_url},
            )
        )
    return candidates


def discover_youtube_api(channel_id: str, *, token: str, limit: int = 25) -> list[Candidate]:
    """List uploads through the official YouTube Data API when a key exists."""
    if not token:
        return []
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(
        {
            "part": "snippet",
            "channelId": channel_id,
            "order": "date",
            "maxResults": min(limit, 50),
            "type": "video",
            "key": token,
        }
    )
    try:
        payload = http_get_json(url)
    except Exception as error:  # noqa: BLE001
        print(f"  youtube api failed: {error}", flush=True)
        return []
    candidates = []
    for item in payload.get("items", []):
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId", "")
        if not video_id:
            continue
        candidates.append(
            Candidate(
                video_id=video_id,
                title=snippet.get("title", ""),
                source="youtube-api",
                url=f"https://www.youtube.com/watch?v={video_id}",
                published=snippet.get("publishedAt", ""),
                confidence=0.95,
            )
        )
    return candidates


def discover_google_cse(query: str, *, cx: str, key: str, limit: int = 20) -> list[Candidate]:
    """Search the web through Google Programmable Search."""
    if not (cx and key):
        return []
    url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
        {"q": query, "cx": cx, "key": key, "num": min(limit, 10), "safe": "active"}
    )
    try:
        payload = http_get_json(url)
    except Exception as error:  # noqa: BLE001
        print(f"  google cse failed: {error}", flush=True)
        return []
    candidates = []
    for item in payload.get("items", []):
        link = item.get("link", "")
        match = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", link)
        candidates.append(
            Candidate(
                video_id=match.group(1) if match else "",
                title=item.get("title", ""),
                source="google-cse",
                url=link,
                confidence=0.6,
                details={"snippet": item.get("snippet", "")[:200]},
            )
        )
    return candidates


def discover_cspan(query: str) -> list[Candidate]:
    """Look for the same event on C-SPAN."""
    try:
        html = http_get(CSPAN_SEARCH.format(query=urllib.parse.quote(query)))
    except Exception as error:  # noqa: BLE001
        print(f"  cspan failed: {error}", flush=True)
        return []
    candidates = []
    for match in re.finditer(
        r'href="(https://www\.c-span\.org/program/[^"]+)"[^>]*>([^<]{6,120})', html
    ):
        candidates.append(
            Candidate(
                title=match.group(2).strip(),
                source="c-span",
                url=match.group(1),
                confidence=0.5,
            )
        )
    return candidates


def discover_factbase_calendar(day: date) -> list[Candidate]:
    """Read the RollCall Factbase calendar to confirm what happened on a day.

    The calendar is a single month page, so the page is fetched once and the
    requested day is located inside it.
    """
    html = _factbase_page()
    if not html:
        return []
    months = {
        1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
        7: "July", 8: "August", 9: "September", 10: "October", 11: "November",
        12: "December",
    }
    label = f"{months[day.month]} {day.day}, {day.year}"
    headings = list(
        re.finditer(
            r"\b(?:January|February|March|April|May|June|July|August|September|October|"
            r"November|December) \d{1,2}, \d{4}",
            html,
        )
    )
    position = next((h.start() for h in headings if h.group(0) == label), -1)
    if position < 0:
        return []
    # The day block sits between the surrounding day headings.
    previous = [h for h in headings if h.end() <= position]
    following = [h for h in headings if h.start() >= position + len(label)]
    start = previous[-1].end() if previous else max(position - 4000, 0)
    end = following[0].start() if following else position + 8000
    block = html[start:end]
    # Event entries are plain text lines, not links.
    text = re.sub(r"<[^>]+>", "\n", block)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    time_marker = re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)$", re.I)
    place_marker = re.compile(r"^(The White House|Camp David|Executive Residence)$", re.I)
    entries: list[Candidate] = []
    index_line = 0
    while index_line < len(lines):
        line = lines[index_line]
        if line.lower().startswith("tbd:"):
            entries.append(
                Candidate(
                    title=line[4:].strip(),
                    source="factbase",
                    url=FACTSBASE_CALENDAR,
                    event_date=day.isoformat(),
                    confidence=0.75,
                    details={"time": "TBD"},
                )
            )
            index_line += 1
            continue
        if time_marker.match(line):
            # The event description follows the second time marker.
            cursor = index_line + 1
            seen_time = 1
            while cursor < len(lines):
                if time_marker.match(lines[cursor]):
                    seen_time += 1
                    cursor += 1
                    continue
                if place_marker.match(lines[cursor]):
                    cursor += 1
                    continue
                break
            if seen_time >= 2 and cursor < len(lines):
                description = lines[cursor]
                if not time_marker.match(description) and description != label:
                    entries.append(
                        Candidate(
                            title=description,
                            source="factbase",
                            url=FACTSBASE_CALENDAR,
                            event_date=day.isoformat(),
                            confidence=0.8,
                            details={"time": line},
                        )
                    )
            index_line = cursor
            continue
        index_line += 1
    seen: set[str] = set()
    unique = []
    for entry in entries:
        if entry.title in seen:
            continue
        seen.add(entry.title)
        unique.append(entry)
    return unique[:40]


_FACTBASE_HTML: str = ""


def _factbase_page() -> str:
    """Fetch the Factbase calendar page once per process."""
    global _FACTBASE_HTML
    if _FACTBASE_HTML:
        return _FACTBASE_HTML
    try:
        _FACTBASE_HTML = http_get(FACTSBASE_CALENDAR, timeout=30)
    except Exception as error:  # noqa: BLE001
        print(f"  factbase failed: {error}", flush=True)
        _FACTBASE_HTML = ""
    return _FACTBASE_HTML


def verify_gdelt(query: str, *, key: str = "", days: int = 7) -> list[dict]:
    """Query GDELT for mainstream coverage of the same event."""
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(
        {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": 50,
            "timespan": f"{days}d",
        }
    )
    if key:
        url += f"&apikey={urllib.parse.quote(key)}"
    try:
        payload = http_get_json(url, timeout=30)
    except Exception as error:  # noqa: BLE001
        print(f"  gdelt failed: {error}", flush=True)
        return []
    articles = []
    for item in payload.get("articles", []):
        articles.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "domain": item.get("domain", ""),
                "date": item.get("seendate", ""),
            }
        )
    return articles


def verify_newsbank(query: str, *, key: str) -> list[dict]:
    """Search NewsBank when a key is configured."""
    if not key:
        return []
    url = "https://api.newsbank.com/v1/search?" + urllib.parse.urlencode({"q": query})
    try:
        payload = http_get_json(url, headers={"Authorization": f"Bearer {key}"})
    except Exception as error:  # noqa: BLE001
        print(f"  newsbank failed: {error}", flush=True)
        return []
    return [
        {"title": item.get("title", ""), "url": item.get("url", ""), "source": "newsbank"}
        for item in payload.get("results", [])
    ]


def verify_congress(query: str) -> list[dict]:
    """Look for congressional records mentioning the same event."""
    url = "https://www.congress.gov/search?" + urllib.parse.urlencode(
        {"q": query, "source": "legislation", "searchResultViewType": "expanded"}
    )
    try:
        html = http_get(url)
    except Exception as error:  # noqa: BLE001
        print(f"  congress failed: {error}", flush=True)
        return []
    results = []
    for match in re.finditer(
        r'<a[^>]+href="(/bill/[^"]+)"[^>]*>\s*([^<]{6,160}?)\s*</a>', html
    ):
        results.append(
            {
                "title": re.sub(r"\s+", " ", match.group(2)).strip(),
                "url": urllib.parse.urljoin("https://www.congress.gov", match.group(1)),
                "source": "congress",
            }
        )
    return results[:25]


def verify_youtube_discussion(video_id: str, *, limit: int = 25) -> list[dict]:
    """Read the public comment thread as a community cross-check."""
    try:
        html = http_get(f"https://www.youtube.com/watch?v={video_id}", timeout=25)
    except Exception as error:  # noqa: BLE001
        print(f"  discussion failed: {error}", flush=True)
        return []
    comments = []
    for match in re.finditer(r'"contentText":\{"simpleText":"(.{10,400}?)"\}\}', html):
        text = match.group(1).encode().decode("unicode_escape", "ignore")
        comments.append({"text": text[:300], "source": "youtube-comments"})
        if len(comments) >= limit:
            break
    return comments


def verify_forums(video_id: str, *, title: str) -> list[dict]:
    """Search Reddit and related forums for the same event."""
    url = "https://www.reddit.com/search.json?" + urllib.parse.urlencode(
        {"q": f'"{title}"', "sort": "new", "limit": 25, "t": "year"}
    )
    try:
        payload = http_get_json(url, headers={"Accept": "application/json"})
    except Exception as error:  # noqa: BLE001
        print(f"  forums failed: {error}", flush=True)
        return []
    posts = []
    for child in payload.get("data", {}).get("children", []):
        data = child.get("data", {})
        posts.append(
            {
                "title": data.get("title", ""),
                "url": "https://www.reddit.com" + data.get("permalink", ""),
                "score": data.get("score", 0),
                "source": "reddit",
            }
        )
    return posts


def verify_backlinks(video_id: str, *, title: str, key: str = "") -> list[dict]:
    """Find pages that link back to the video."""
    results: list[dict] = []
    if key:
        url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
            {
                "q": f'link:"youtube.com/watch?v={video_id}" OR "{title}"',
                "cx": key,
                "key": key,
                "num": 10,
            }
        )
        try:
            payload = http_get_json(url)
            for item in payload.get("items", []):
                results.append(
                    {"title": item.get("title", ""), "url": item.get("link", ""), "source": "backlink"}
                )
        except Exception as error:  # noqa: BLE001
            print(f"  backlinks failed: {error}", flush=True)
    for post in verify_forums(video_id, title=title):
        results.append(post)
    return results


def rank_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Prefer long, multi-angle, high-confidence records of the same event."""
    grouped: dict[str, Candidate] = {}
    for candidate in candidates:
        key = candidate.video_id or candidate.url or candidate.title
        if key not in grouped:
            grouped[key] = candidate
            continue
        existing = grouped[key]
        existing.confidence = max(existing.confidence, candidate.confidence)
        existing.details.setdefault("also_seen_in", []).append(candidate.source)
        if candidate.duration > existing.duration:
            existing.duration = candidate.duration
    ordered = list(grouped.values())
    ordered.sort(key=lambda c: (-c.confidence, -c.duration, c.title))
    return ordered


def main() -> int:
    """Print every source's current view so a human can compare them."""
    print("== YouTube channels ==")
    for name, url in YOUTUBE_CHANNELS.items():
        found = discover_youtube_channel(url, limit=8)
        print(f"  {name}: {len(found)} uploads")
        for item in found[:5]:
            print(f"    {item.published} {item.video_id} {item.title[:70]}")
    print("== Factbase calendar (today) ==")
    for item in discover_factbase_calendar(date.today())[:8]:
        print(f"    {item.title[:80]}")
    print("== GDELT coverage (last 7 days) ==")
    for article in verify_gdelt('"white house" president', days=7)[:8]:
        print(f"    {article['domain']}: {article['title'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
