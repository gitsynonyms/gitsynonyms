#!/usr/bin/env python3
"""Discover proxy sources by researching proxy terminology across forums and social.

Stage 0 of the proxy index. `build_index.py` only knows the GitHub topics and
name patterns encoded in its query list, so it misses sources that are described
with other words. This stage enumerates synonyms, near-synonyms and word
permutations for "network proxy" (English and Chinese), asks the Last30Days
skill to research each term across forums, social platforms and search engines,
and records the source URLs it finds.

Only URLs that were actually returned by the research are written. A term whose
research failed is recorded with ``ok: false`` and contributes no sources, so a
missing result is never replaced by a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

OUT = Path(__file__).resolve().parent
RESEARCH = OUT / "term_research.jsonl"
EXTRA = OUT / "sources_extra.txt"
SOURCES = OUT / "sources.txt"

# Generic head words: the object being listed.
HEAD_TERMS = [
    "proxy",
    "proxies",
    "proxy list",
    "proxy lists",
    "proxylist",
    "proxy-list",
    "proxy_list",
    "proxy pool",
    "proxy server",
    "proxy servers",
    "proxy database",
    "ip pool",
    "网络代理",
    "代理",
    "代理服务器",
    "代理列表",
    "代理清单",
    "代理池",
    "代理IP",
    "IP代理",
    "IP池",
]

# Qualifiers that turn a head word into a narrower, commonly used phrase.
QUALIFIERS = [
    "free",
    "public",
    "open",
    "anonymous",
    "anonymity",
    "elite",
    "transparent",
    "high anonymity",
    "http",
    "https",
    "socks",
    "socks4",
    "socks4a",
    "socks5",
    "socks5h",
    "rotating",
    "residential",
    "datacenter",
    "mobile",
    "static",
    "dynamic",
    "working",
    "live",
    "valid",
    "fresh",
    "daily",
    "updated",
    "checked",
    "verified",
    "免费",
    "公共",
    "公开",
    "开放",
    "匿名",
    "高匿",
    "透明",
    "轮换",
    "动态",
    "静态",
    "住宅",
    "机房",
    "数据中心",
    "移动",
    "可用",
    "有效",
    "在线",
    "最新",
    "每日",
]

# Actions people use when they publish or consume proxy lists.
ACTIONS = [
    "scraper",
    "crawler",
    "grabber",
    "collector",
    "aggregator",
    "harvester",
    "checker",
    "validator",
    "tester",
    "verifier",
    "抓取",
    "爬虫",
    "采集",
    "收集",
    "聚合",
    "汇总",
    "检测",
    "验证",
    "测试",
    "筛选",
]


# Forum, social and search-engine scopes. Last30Days already sweeps X/Twitter,
# Reddit and Hacker News; these hints make the wording explicit for engines
# that understand site: filters and record which scope produced which source.
SCOPE_HINTS = [
    "site:reddit.com",
    "site:news.ycombinator.com",
    "site:stackoverflow.com",
    "site:github.com",
    "site:v2ex.com",
    "site:hostloc.com",
    "site:medium.com",
    "forum",
    "telegram",
]


QUERY_GROUPS = ("core", "qualifier", "action", "permutation", "chinese", "scope")


def build_queries(limit: int = 0, groups: Iterable[str] | None = None) -> list[str]:
    """Enumerate synonym, near-synonym and permutation queries.

    ``groups`` selects which blocks to include; ``None`` means every block. The
    order is deterministic and duplicates are removed, so the same arguments
    always produce the same research plan.
    """
    selected = set(groups) if groups else set(QUERY_GROUPS)
    unknown = selected - set(QUERY_GROUPS)
    if unknown:
        raise ValueError(f"unknown query groups: {', '.join(sorted(unknown))}")
    queries: list[str] = []

    def add(value: str) -> None:
        normalized = " ".join(value.split())
        if normalized and normalized not in queries:
            queries.append(normalized)

    if "core" in selected:
        for head in HEAD_TERMS:
            add(head)
    if "qualifier" in selected:
        for qualifier in QUALIFIERS:
            add(f"{qualifier} proxy")
            add(f"{qualifier} proxy list")
    if "action" in selected:
        for action in ACTIONS:
            add(f"proxy {action}")
    if "permutation" in selected:
        for qualifier in QUALIFIERS:
            for head in ("proxy list", "proxy pool", "proxies"):
                add(f"{qualifier} {head}")
    if "chinese" in selected:
        for qualifier in ("免费", "公共", "匿名", "高匿", "可用", "每日", "轮换", "住宅"):
            for head in ("代理", "代理列表", "代理池", "代理IP"):
                add(f"{qualifier}{head}")
    if "scope" in selected:
        base = list(queries[:60]) or list(HEAD_TERMS)
        for scope in SCOPE_HINTS:
            for value in base:
                add(f"{value} {scope}")
    return queries[:limit] if limit and limit > 0 else queries


def run_last30days(
    query: str, save_dir: Path, *, timeout: int = 300
) -> tuple[bool, dict[str, Any]]:
    """Invoke the Last30Days skill for one query and parse its JSON payload."""
    script = os.environ.get("LAST30DAYS_SCRIPT")
    if script:
        command = [
            sys.executable,
            script,
            query,
            "--research-only",
            "--save-dir",
            str(save_dir),
            "--emit=json",
        ]
    else:
        command = [
            "last30days",
            "research",
            query,
            "--save-dir",
            str(save_dir),
            "--format",
            "json",
            "--max-results",
            "20",
            "--recency-days",
            "30",
        ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, {"error": "last30days not installed"}
    except subprocess.TimeoutExpired:
        return False, {"error": "last30days timed out"}
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:500]
        return False, {"error": detail or f"exit code {result.returncode}"}
    try:
        return True, json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return False, {"error": f"invalid JSON: {error}"}


def extract_source_urls(data: dict[str, Any]) -> list[str]:
    """Collect every absolute source URL from one research payload, deduplicated."""
    urls: list[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        url = value.strip()
        if not url.startswith(("http://", "https://")):
            return
        if url not in urls:
            urls.append(url)

    for key in ("sources", "results", "findings", "articles", "items"):
        for item in data.get(key) or []:
            if isinstance(item, dict):
                add(item.get("url"))
                add(item.get("link"))
                add(item.get("permalink"))
            else:
                add(item)
    for cluster in data.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        for item in cluster.get("sources") or []:
            if isinstance(item, dict):
                add(item.get("url"))
            else:
                add(item)
    return urls


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def check_prereqs() -> list[str]:
    """Report missing research tooling instead of silently skipping research."""
    problems: list[str] = []
    script = os.environ.get("LAST30DAYS_SCRIPT")
    if script:
        if not Path(script).is_file():
            problems.append(f"last30days script not found: {script}")
    elif shutil.which("last30days") is None:
        problems.append(
            "last30days CLI not in PATH; set LAST30DAYS_SCRIPT or use --dry-run to list terms only"
        )
    return problems


def research_terms(
    queries: Iterable[str], *, save_dir: Path, sleep: float = 0.0
) -> tuple[list[dict[str, Any]], list[str]]:
    """Research every query and return (per-query records, unique source URLs)."""
    records: list[dict[str, Any]] = []
    discovered: list[str] = []
    for index, query in enumerate(queries, 1):
        ok, data = run_last30days(query, save_dir)
        urls = extract_source_urls(data) if ok else []
        records.append(
            {
                "observed_at": utc_now_iso(),
                "query": query,
                "ok": ok,
                "error": "" if ok else str(data.get("error", "")),
                "source_count": len(urls),
                "urls": urls,
            }
        )
        for url in urls:
            if url not in discovered:
                discovered.append(url)
        print(f"  [{index}] {query} -> {len(urls)} source(s)", flush=True)
        if sleep > 0:
            time.sleep(sleep)
    return records, discovered


def write_research(records: Iterable[dict[str, Any]], path: Path = RESEARCH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_extra(urls: Iterable[str], path: Path = EXTRA) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = list(urls)
    path.write_text("\n".join(values) + ("\n" if values else ""), encoding="utf-8")


def merge_into_sources(urls: Iterable[str], path: Path = SOURCES) -> int:
    """Append only URLs that are not already in sources.txt; return the count added."""
    existing: set[str] = set()
    if path.exists():
        existing = {
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    added = [url for url in urls if url not in existing]
    if added:
        with path.open("a", encoding="utf-8") as handle:
            for url in added:
                handle.write(url + "\n")
    return len(added)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terms", nargs="*", default=[], help="Extra research terms to include")
    parser.add_argument(
        "--groups",
        nargs="*",
        choices=QUERY_GROUPS,
        default=[],
        help="Query blocks to research; omit for all blocks",
    )
    parser.add_argument("--limit", type=int, default=0, help="Research at most this many queries")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds between research calls")
    parser.add_argument("--dry-run", action="store_true", help="List the queries without researching")
    parser.add_argument("--merge", action="store_true", help="Append new URLs to sources.txt")
    parser.add_argument("--output", default=str(RESEARCH), help="Per-query research log (JSONL)")
    parser.add_argument("--extra", default=str(EXTRA), help="Discovered URL list")
    args = parser.parse_args(argv)

    queries = build_queries(args.limit, args.groups or None)
    for term in args.terms:
        normalized = " ".join(term.split())
        if normalized and normalized not in queries:
            queries.append(normalized)
            if args.limit > 0:
                queries = queries[: args.limit]

    print(f"queries: {len(queries)}", flush=True)
    if args.dry_run:
        for query in queries:
            print(query)
        return 0

    problems = check_prereqs()
    if problems:
        for problem in problems:
            print(f"::error::{problem}", file=sys.stderr)
        return 1

    run_dir = OUT / ".term-runs" / utc_now_iso().replace(":", "-")
    run_dir.mkdir(parents=True, exist_ok=True)
    records, discovered = research_terms(queries, save_dir=run_dir, sleep=args.sleep)
    write_research(records, Path(args.output))
    write_extra(discovered, Path(args.extra))
    answered = sum(1 for record in records if record["ok"])
    print(
        f"research: {answered}/{len(records)} queries answered, "
        f"{len(discovered)} unique source URL(s)",
        flush=True,
    )
    print(f"log: {args.output}")
    print(f"new sources: {args.extra}")
    if args.merge:
        added = merge_into_sources(discovered)
        print(f"merged {added} new URL(s) into {SOURCES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
