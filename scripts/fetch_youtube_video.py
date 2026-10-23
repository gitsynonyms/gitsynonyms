#!/usr/bin/env python3
"""Download a YouTube video when direct media access is blocked.

YouTube signs media URLs for the requesting IP and rejects datacenter egress, so
this helper probes public proxy lists until one can both extract the player
response and serve the media bytes, then hands that proxy to yt-dlp.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

HTTP_LISTS = (
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
)
SOCKS_LISTS = (
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
)
FORMAT = "136+140/bv*[height<=720]+ba/b"


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _curl_args(scheme: str, proxy: str) -> list[str]:
    return ["-x", f"http://{proxy}"] if scheme == "http" else ["--socks5-hostname", proxy]


def _ytdlp_proxy(scheme: str, proxy: str) -> str:
    return proxy if scheme == "http" else f"socks5://{proxy}"


def refresh_pool(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for name, urls in (("pool_http.txt", HTTP_LISTS), ("pool_socks5.txt", SOCKS_LISTS)):
        lines: list[str] = []
        for url in urls:
            result = _run(["curl", "-sL", "--max-time", "60", url], timeout=75)
            if result and result.stdout:
                lines.extend(result.stdout.split())
        entries = [line for line in dict.fromkeys(lines) if line.count(":") == 1]
        (cache / name).write_text("\n".join(entries))
        print(f"{name}: {len(entries)} candidates", flush=True)


def candidates(cache: Path) -> list[tuple[str, str]]:
    entries = []
    for name, scheme in (("pool_http.txt", "http"), ("pool_socks5.txt", "socks5")):
        path = cache / name
        if not path.exists():
            continue
        entries.extend((line.strip(), scheme) for line in path.read_text().split() if line.count(":") == 1)
    return entries


def alive(entry: tuple[str, str], timeout: int) -> tuple[str, str, float] | None:
    proxy, scheme = entry
    result = _run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{time_total}", "--max-time", str(timeout),
         *_curl_args(scheme, proxy), "https://www.gstatic.com/generate_204"],
        timeout=timeout + 3,
    )
    if not result or result.returncode != 0:
        return None
    try:
        return proxy, scheme, float(result.stdout.strip() or 99)
    except ValueError:
        return None


def extract_media(entry: tuple[str, str], url: str) -> tuple[str, str, str] | None:
    proxy, scheme = entry
    result = _run(
        ["yt-dlp", "--js-runtimes", "node", "--no-warnings", "--proxy", _ytdlp_proxy(scheme, proxy),
         "-g", "-f", FORMAT, url],
        timeout=90,
    )
    if not result or result.returncode != 0 or not result.stdout.strip():
        return None
    return proxy, scheme, result.stdout.strip().splitlines()[0]


def media_ok(entry: tuple[str, str], media: str) -> bool:
    proxy, scheme = entry
    result = _run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "30",
         "-r", "0-3000000", *_curl_args(scheme, proxy), media],
        timeout=40,
    )
    return bool(result) and result.stdout.strip() in {"200", "206"}


def reusable(cache: Path, url: str) -> tuple[str, str] | None:
    path = cache / "usable.txt"
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        entry = (parts[1], parts[0])
        found = extract_media(entry, url)
        if found and media_ok(entry, found[2]):
            print(f"reusing saved proxy {parts[1]}", flush=True)
            return entry
    return None


def find_proxy(url: str, cache: Path, workers: int, limit: int, timeout: int) -> tuple[str, str] | None:
    entry = reusable(cache, url)
    if entry:
        return entry
    pool = candidates(cache)[:limit]
    if not pool:
        print("proxy pool is empty; run with --refresh", file=sys.stderr)
        return None
    print(f"probing {len(pool)} proxies", flush=True)
    live: list[tuple[str, str, float]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for index, result in enumerate(executor.map(lambda item: alive(item, timeout), pool), 1):
            if result:
                live.append(result)
            if index % 2000 == 0:
                print(f"  {index} checked, {len(live)} alive", flush=True)
    live.sort(key=lambda row: row[2])
    print(f"{len(live)} alive; testing extraction", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, workers // 8)) as executor:
        futures = [executor.submit(extract_media, (proxy, scheme), url) for proxy, scheme, _ in live]
        for future in concurrent.futures.as_completed(futures):
            found = future.result()
            if not found:
                continue
            if media_ok((found[0], found[1]), found[2]):
                print(f"using proxy {found[0]} ({found[1]})", flush=True)
                cache.mkdir(parents=True, exist_ok=True)
                (cache / "usable.txt").write_text(f"{found[1]} {found[0]}\n")
                return found[0], found[1]
    return None


def direct_media_ok(url: str) -> bool:
    probe = _run(["yt-dlp", "--js-runtimes", "node", "--no-warnings", "-g", "-f", FORMAT, url], timeout=90)
    if not probe or probe.returncode != 0 or not probe.stdout.strip():
        return False
    media = probe.stdout.strip().splitlines()[0]
    result = _run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "20",
                   "-r", "0-1000", media], timeout=25)
    return bool(result) and result.stdout.strip() in {"200", "206"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download a YouTube video through a working proxy.")
    parser.add_argument("url")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--workers", type=int, default=96)
    parser.add_argument("--limit", type=int, default=6000)
    parser.add_argument("--timeout", type=int, default=6)
    parser.add_argument("--cache", default=str(Path.home() / ".cache" / "yt-proxy"))
    parser.add_argument("--refresh", action="store_true", help="Refetch proxy lists before probing.")
    args = parser.parse_args(argv)

    cache = Path(args.cache)
    target = Path(args.output_dir)
    target.mkdir(parents=True, exist_ok=True)
    if args.refresh:
        refresh_pool(cache)

    proxy_flag = None
    if direct_media_ok(args.url):
        print("direct media access works; no proxy needed", flush=True)
    else:
        entry = find_proxy(args.url, cache, args.workers, args.limit, args.timeout)
        if not entry:
            print("no usable proxy found", file=sys.stderr)
            return 2
        proxy_flag = _ytdlp_proxy(entry[1], entry[0])

    command = ["yt-dlp", "--js-runtimes", "node", "--no-warnings", "--retries", "10",
               "--fragment-retries", "10", "--continue", "-f", FORMAT,
               "--merge-output-format", "mp4", "-o", str(target / "source.%(ext)s"), args.url]
    if proxy_flag:
        command[4:4] = ["--proxy", proxy_flag]
    result = _run(command, timeout=60 * 120)
    if result is None or result.returncode != 0:
        print((result.stderr if result else "download timed out")[-800:], file=sys.stderr)
        return 1
    files = [str(path) for path in sorted(target.glob("source.*")) if path.is_file()]
    print(json.dumps({"files": files}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

