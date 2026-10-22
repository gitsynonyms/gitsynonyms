#!/usr/bin/env python3
"""Fetch every source from sources.txt and merge the proxies into one pool.

Stage 2 of the proxy index. Entries are written to raw.txt and deduplicated with
the system sort so a multi-million line pool never has to fit in memory.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(__file__).resolve().parent
SOURCES = OUT / "sources.txt"
RAW = OUT / "raw.txt"
POOL = OUT / "proxies.txt"
STATS = OUT / "collect.jsonl"

IP_PORT = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3}):(\d{2,5})(?![\d])")
SCHEME = re.compile(r"^(?:https?|socks[45]h?|socks)://", re.I)
MAX_BYTES = 4_000_000


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "proxyindex/1.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = response.read(MAX_BYTES)
    return payload.decode("utf-8", "replace")


def parse(text: str) -> list[str]:
    entries: list[str] = []
    lowered = text.lstrip()[:1]
    if lowered in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    ip = item.get("ip") or item.get("host") or item.get("proxy")
                    port = item.get("port")
                    if ip and port:
                        entries.append(f"{ip}:{port}")
                elif isinstance(item, str):
                    entries.append(item)
    for match in IP_PORT.finditer(text):
        octets = [int(part) for part in match.group(1).split(".")]
        if any(part > 255 for part in octets) or octets[0] == 0:
            continue
        port = int(match.group(2))
        if not 1 <= port <= 65535:
            continue
        entries.append(f"{match.group(1)}:{port}")
    cleaned = []
    for entry in entries:
        entry = SCHEME.sub("", entry.strip())
        if IP_PORT.fullmatch(entry):
            cleaned.append(entry)
    return cleaned


def main() -> int:
    urls = [line.strip() for line in SOURCES.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"sources: {len(urls)}", flush=True)
    processed = 0
    with RAW.open("w", encoding="utf-8") as raw, STATS.open("w", encoding="utf-8") as stats:

        def work(url: str) -> tuple[str, str | None, int]:
            try:
                text = fetch(url)
            except Exception as error:  # noqa: BLE001 - report and continue
                return url, f"{type(error).__name__}: {str(error)[:80]}", 0
            entries = parse(text)
            if entries:
                raw.write("\n".join(entries) + "\n")
                raw.flush()
            return url, None, len(entries)

        with ThreadPoolExecutor(max_workers=12) as pool:
            for url, error, count in pool.map(work, urls):
                stats.write(json.dumps({"url": url, "error": error, "entries": count}) + "\n")
                stats.flush()
                processed += 1
                if processed % 100 == 0:
                    print(f"  fetched {processed}/{len(urls)}", flush=True)

    print("deduplicating with sort -u ...", flush=True)
    with POOL.open("w", encoding="utf-8") as out:
        subprocess.run(
            ["sort", "-u", "-S", "512M", "-T", str(OUT), str(RAW)],
            stdout=out,
            check=True,
        )
    unique = sum(1 for _ in POOL.open(encoding="utf-8"))
    errors = sum(1 for line in STATS.open(encoding="utf-8") if json.loads(line)["error"])
    print(f"unique proxies: {unique}")
    print(f"sources with entries: {len(urls) - errors}, failed: {errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
