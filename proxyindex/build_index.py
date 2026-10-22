#!/usr/bin/env python3
"""Discover proxy-list sources (a list of lists) through the GitHub search API.

Stage 1 of the proxy index: find candidate repositories, then list the files in
each repository that plausibly hold proxies, and write them to sources.txt.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent
SOURCES = OUT / "sources.txt"
META = OUT / "sources.jsonl"

QUERIES = [
    "topic:proxy-list",
    "topic:proxies",
    "topic:free-proxy",
    "topic:proxy-scraper",
    "topic:proxy-checker",
    "topic:socks5",
    "proxy-list in:name",
    "proxy list in:description",
    '"free proxy list" in:description',
    "proxy scraper in:name",
]

FILE_HINT = re.compile(
    r"(?i)(proxy|proxies|http|https|socks|sock5|ip|list|checked|valid|live|worked|working|good|fresh|all|\bus\b)"
)
COUNTRY = re.compile(r"^(?:[A-Za-z]{2}|all)\.(?:txt|csv|json)$")
SKIP_EXT = {".md", ".py", ".js", ".ts", ".go", ".rs", ".java", ".php", ".html", ".png", ".jpg", ".svg", ".lock", ".yml", ".yaml", ".toml", ".cfg", ".ini"}


def gh(args: list[str]) -> str:
    result = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:200])
    return result.stdout


def search(query: str, pages: int, sort: str) -> list[dict]:
    repos: list[dict] = []
    for page in range(1, pages + 1):
        try:
            raw = gh([
                "-X", "GET", "search/repositories",
                "-f", f"q={query}", "-f", "per_page=100", "-f", f"page={page}",
                "-f", f"sort={sort}", "-f", "order=desc",
            ])
        except RuntimeError as error:
            print(f"  search failed {query} p{page}: {error}", flush=True)
            time.sleep(20)
            continue
        items = json.loads(raw).get("items", [])
        if not items:
            break
        repos.extend(items)
        time.sleep(2.2)  # search API allows 30 requests/minute
    return repos


def main() -> int:
    cutoff = (date.today() - timedelta(days=540)).isoformat()
    seen: dict[str, dict] = {}
    for query in QUERIES:
        for sort, pages in (("updated", 8), ("stars", 4)):
            for repo in search(query, pages, sort):
                name = repo.get("full_name")
                if not name or name in seen:
                    continue
                if repo.get("archived") or repo.get("fork"):
                    continue
                if (repo.get("pushed_at") or "") < cutoff:
                    continue
                if repo.get("size", 0) > 200_000:
                    continue
                seen[name] = {
                    "repo": name,
                    "stars": repo.get("stargazers_count", 0),
                    "pushed_at": repo.get("pushed_at"),
                    "branch": repo.get("default_branch") or "main",
                    "query": query,
                }
        print(f"{query:38s} total repos so far: {len(seen)}", flush=True)

    print(f"candidate repositories: {len(seen)}", flush=True)

    sources: list[dict] = []
    for index, (name, info) in enumerate(sorted(seen.items()), 1):
        try:
            tree = json.loads(gh([f"repos/{name}/git/trees/{info['branch']}?recursive=1"]))
        except RuntimeError as error:
            print(f"  tree failed {name}: {error}", flush=True)
            time.sleep(3)
            continue
        for blob in tree.get("tree", []):
            path = blob.get("path", "")
            if blob.get("type") != "blob" or not path:
                continue
            suffix = Path(path).suffix.lower()
            if suffix in SKIP_EXT:
                continue
            base = Path(path).name
            if not (FILE_HINT.search(base) or COUNTRY.match(base)):
                continue
            if blob.get("size", 0) > 3_000_000:
                continue
            sources.append({
                "repo": name,
                "stars": info["stars"],
                "pushed_at": info["pushed_at"],
                "path": path,
                "size": blob.get("size", 0),
                "url": f"https://raw.githubusercontent.com/{name}/{info['branch']}/{path}",
            })
        if index % 25 == 0:
            print(f"  scanned {index}/{len(seen)} repos, {len(sources)} files", flush=True)
        time.sleep(0.15)

    unique = {item["url"]: item for item in sources}
    SOURCES.write_text("\n".join(sorted(unique)) + "\n", encoding="utf-8")
    with META.open("w", encoding="utf-8") as handle:
        for item in sorted(unique.values(), key=lambda x: x["url"]):
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"sources written: {len(unique)} -> {SOURCES}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
