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

# Core topics (GitHub topics taxonomy)
TOPIC_QUERIES = [
    "topic:proxy-list",
    "topic:proxies",
    "topic:proxy",
    "topic:free-proxy",
    "topic:free-proxies",
    "topic:public-proxy",
    "topic:open-proxy",
    "topic:http-proxy",
    "topic:https-proxy",
    "topic:socks-proxy",
    "topic:socks4",
    "topic:socks5",
    "topic:proxy-scraper",
    "topic:proxy-crawler",
    "topic:proxy-grabber",
    "topic:proxy-collector",
    "topic:proxy-aggregator",
    "topic:proxy-pool",
    "topic:ip-pool",
    "topic:proxy-checker",
    "topic:proxy-validator",
    "topic:proxy-tester",
    "topic:proxy-verifier",
    "topic:working-proxy",
    "topic:live-proxy",
    "topic:valid-proxy",
    "topic:fresh-proxy",
    "topic:daily-proxy",
    "topic:updated-proxy",
    "topic:proxy-list-daily",
    "topic:free-proxy-list",
    "topic:proxy-database",
    "topic:proxy-server",
    "topic:anonymity",
    "topic:anonymity-network",
]

# Name / title queries (repo name contains)
NAME_QUERIES = [
    "proxy-list in:name",
    "proxy list in:name",
    "proxylist in:name",
    "proxy_list in:name",
    "free-proxy in:name",
    "free-proxy-list in:name",
    "free-proxies in:name",
    "public-proxy in:name",
    "open-proxy in:name",
    "http-proxy in:name",
    "https-proxy in:name",
    "socks-proxy in:name",
    "socks4 in:name",
    "socks5 in:name",
    "socks5-proxy in:name",
    "proxy-scraper in:name",
    "proxy-crawler in:name",
    "proxy-grabber in:name",
    "proxy-collector in:name",
    "proxy-aggregator in:name",
    "proxy-pool in:name",
    "ip-pool in:name",
    "proxy-checker in:name",
    "proxy-validator in:name",
    "proxy-tester in:name",
    "proxy-verifier in:name",
    "working-proxy in:name",
    "live-proxy in:name",
    "valid-proxy in:name",
    "fresh-proxy in:name",
    "daily-proxy in:name",
    "updated-proxy in:name",
    "proxy-database in:name",
    "proxy-server in:name",
    "proxyip in:name",
    "proxy-ip in:name",
    "proxylists in:name",
    "proxy-lists in:name",
    "freeproxylist in:name",
    "free-proxylist in:name",
    "proxylist-daily in:name",
]

# Description queries (repo description contains)
DESC_QUERIES = [
    "proxy list in:description",
    "free proxy list in:description",
    "public proxy in:description",
    "open proxy in:description",
    "http proxy in:description",
    "https proxy in:description",
    "socks proxy in:description",
    "socks5 proxy in:description",
    "socks4 proxy in:description",
    "proxy scraper in:description",
    "proxy crawler in:description",
    "proxy grabber in:description",
    "proxy collector in:description",
    "proxy aggregator in:description",
    "proxy pool in:description",
    "ip pool in:description",
    "proxy checker in:description",
    "proxy validator in:description",
    "proxy tester in:description",
    "proxy verifier in:description",
    "working proxy in:description",
    "live proxy in:description",
    "valid proxy in:description",
    "fresh proxy in:description",
    "daily proxy in:description",
    "updated proxy in:description",
    "proxy database in:description",
    "free proxy in:description",
    "anonymous proxy in:description",
    "high anonymity proxy in:description",
    "elite proxy in:description",
    "transparent proxy in:description",
    "rotating proxy in:description",
    "residential proxy in:description",
    "datacenter proxy in:description",
    "proxy ip in:description",
    "proxy list daily in:description",
    "daily proxy list in:description",
    "updated proxy list in:description",
]

# Chinese / localized synonyms that appear in GitHub repos
CN_QUERIES = [
    "代理 in:name,description",
    "代理列表 in:name,description",
    "免费代理 in:name,description",
    "公共代理 in:name,description",
    "开放代理 in:name,description",
    "代理池 in:name,description",
    "IP池 in:name,description",
    "代理IP in:name,description",
    "代理抓取 in:name,description",
    "代理爬虫 in:name,description",
    "代理采集 in:name,description",
    "代理聚合 in:name,description",
    "代理合集 in:name,description",
    "代理大全 in:name,description",
    "代理收集 in:name,description",
    "代理检测 in:name,description",
    "代理验证 in:name,description",
    "代理测试 in:name,description",
    "代理筛选 in:name,description",
    "可用代理 in:name,description",
    "有效代理 in:name,description",
    "新鲜代理 in:name,description",
    "每日代理 in:name,description",
    "更新代理 in:name,description",
    "SOCKS代理 in:name,description",
    "HTTP代理 in:name,description",
    "HTTPS代理 in:name,description",
    "高匿代理 in:name,description",
    "匿名代理 in:name,description",
    "透明代理 in:name,description",
    "住宅代理 in:name,description",
    "机房代理 in:name,description",
]

# Combination / permutation queries (multiple keywords)
COMBO_QUERIES = [
    "proxy list free in:name,description",
    "proxy list daily in:name,description",
    "free proxy list daily in:name,description",
    "public proxy list free in:name,description",
    "working proxy list in:name,description",
    "live proxy list in:name,description",
    "valid proxy list in:name,description",
    "fresh proxy list in:name,description",
    "socks5 proxy list in:name,description",
    "http proxy list in:name,description",
    "https proxy list in:name,description",
    "proxy checker list in:name,description",
    "proxy validator list in:name,description",
    "proxy scraper list in:name,description",
    "proxy pool list in:name,description",
    "ip pool proxy in:name,description",
    "anonymous proxy list in:name,description",
    "elite proxy list in:name,description",
    "high anonymity proxy in:name,description",
    "rotating proxy list in:name,description",
    "residential proxy list in:name,description",
    "datacenter proxy list in:name,description",
    "proxy database free in:name,description",
    "proxy server list in:name,description",
]

# Assemble master list
QUERIES = TOPIC_QUERIES + NAME_QUERIES + DESC_QUERIES + CN_QUERIES + COMBO_QUERIES

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
