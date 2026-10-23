from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

from . import dates
from dataclasses import dataclass

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"

DISCOVERY_LIMITS = {
    "default": 10,
    "shallow": 5,
    "deep": 25,
    "exhaustive": 50,
}

@dataclass
class SourceResult:
    title: str
    url: str
    source: str
    published_at: str
    summary: str = ""

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"

DISCOVERY_LIMITS = {
    "default": 10,
    "shallow": 5,
    "deep": 25,
    "exhaustive": 50,
}


def _date_to_unix(date_str: str) -> int:
    """Convert YYYY-MM-DD to Unix timestamp."""
    from datetime import datetime, timezone
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def discover_hackernews(
    *,
    topic: str,
    depth: str,
    lookback_days: int = 30,
    as_of_date: str | None = None,
    limit: int | None = None,
) -> list:
    """Discover Hacker News stories related to a topic."""
    limit = limit or DISCOVERY_LIMITS.get("default", 10)
    from_date = dates.get_date_range(lookback_days, as_of_date=as_of_date)[0]
    to_date = dates.get_date_range(lookback_days, as_of_date=as_of_date)[1]
    from_ts = _date_to_unix(from_date)
    to_ts = _date_to_unix(to_date) + 86400

    urls = [
        f"{ALGOLIA_SEARCH_URL}?{urlencode({'tags': 'front_page', 'hitsPerPage': str(limit)})}",
        f"{ALGOLIA_SEARCH_URL}?{urlencode({'tags': 'story', 'numericFilters': f'created_at_i>{from_ts},created_at_i<{to_ts}', 'hitsPerPage': str(limit)})}",
    ]

    import httpx
    hits: list[dict[str, Any]] = []
    for url in urls:
        try:
            resp = httpx.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            hits.extend(data.get("hits", []))
        except Exception:
            pass

    results = []
    for hit in hits[:limit]:
        title = hit.get("title", "")
        url = hit.get("url", "") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if topic.lower() in title.lower() or topic.lower() in hit.get("story_text", "").lower():
            results.append(SourceResult(
                title=title,
                url=url,
                source="hackernews",
                published_at=str(hit.get("created_at_i", "")),
                summary=hit.get("story_text", "")[:500],
            ))
    return results
