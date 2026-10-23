#!/usr/bin/env python3
"""Fetch and probe a bounded set of public HTTP proxies for YouTube downloads."""

from __future__ import annotations

import concurrent.futures
import http.client
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable


PROXY_LISTS = {
    "TheSpeedX": "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "monosans": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "proxifly": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
}
PROBE_URL = "https://www.youtube.com/generate_204"
FETCH_TIMEOUT_SECONDS = 12
PROBE_TIMEOUT_SECONDS = 4
MAX_CANDIDATES_PER_SOURCE = 10
MAX_SELECTED_PROXIES = 3
MAX_WORKERS = 15
IP_PORT_PATTERN = re.compile(r"^(?:https?://)?(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})/?$")


def normalize_candidate(line: str) -> str | None:
    match = IP_PORT_PATTERN.fullmatch(line.strip())
    if not match:
        return None
    address, port_text = match.groups()
    try:
        ipaddress.IPv4Address(address)
        port = int(port_text)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return f"http://{address}:{port}"


def fetch_candidates(
    open_url: Callable[..., object] = urllib.request.urlopen,
) -> dict[str, list[str]]:
    candidates_by_source: dict[str, list[str]] = {}
    for source, url in PROXY_LISTS.items():
        try:
            with open_url(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
                content = response.read().decode("utf-8", errors="replace")
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            print(f"Warning: could not fetch {source} proxy list: {error}", file=sys.stderr)
            continue
        candidates: list[str] = []
        seen: set[str] = set()
        for line in content.splitlines():
            proxy = normalize_candidate(line)
            if proxy and proxy not in seen:
                seen.add(proxy)
                candidates.append(proxy)
            if len(candidates) >= MAX_CANDIDATES_PER_SOURCE:
                break
        if candidates:
            candidates_by_source[source] = candidates
        else:
            print(f"Warning: {source} proxy list returned no valid HTTP candidates.", file=sys.stderr)
    if len(candidates_by_source) < 2:
        print(
            f"Warning: only {len(candidates_by_source)} public proxy lists returned "
            "candidates; direct access remains available.",
            file=sys.stderr,
        )
    return candidates_by_source


def probe_proxy(proxy: str) -> float | None:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
    request = urllib.request.Request(
        PROBE_URL,
        headers={"User-Agent": "gitsynonyms-youtube-proxy-check/1.0"},
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            if response.status != 204:
                return None
    except (OSError, urllib.error.URLError, http.client.HTTPException, TimeoutError):
        return None
    return time.monotonic() - started


def select_proxies(
    candidates_by_source: dict[str, list[str]],
    check_proxy: Callable[[str], float | None] = probe_proxy,
) -> tuple[list[str], dict[str, int]]:
    candidate_sources: dict[str, set[str]] = {}
    for source, candidates in candidates_by_source.items():
        for proxy in candidates:
            candidate_sources.setdefault(proxy, set()).add(source)

    results: list[tuple[float, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(check_proxy, proxy): proxy
            for proxy in candidate_sources
        }
        for future in concurrent.futures.as_completed(futures):
            proxy = futures[future]
            try:
                latency = future.result()
            except Exception as error:
                raise RuntimeError(f"Proxy probe failed unexpectedly: {error}") from error
            if latency is not None:
                results.append((latency, proxy))

    results.sort()
    selected = [proxy for _, proxy in results[:MAX_SELECTED_PROXIES]]
    source_counts = {
        source: sum(source in candidate_sources[proxy] for proxy in selected)
        for source in candidates_by_source
    }
    return selected, source_counts


def write_github_env(name: str, value: str, env_file: str) -> None:
    delimiter = f"GH_ENV_{uuid.uuid4().hex}"
    if "\n" in value or "\r" in value or delimiter in value:
        raise ValueError("Environment value contains unsupported line breaks.")
    with open(env_file, "a", encoding="utf-8", newline="\n") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main() -> int:
    env_file = os.environ.get("GITHUB_ENV")
    if not env_file:
        print("GITHUB_ENV is not set; run this selector inside GitHub Actions.", file=sys.stderr)
        return 1
    try:
        candidates = fetch_candidates()
        selected, source_counts = select_proxies(candidates)
        private_proxy = os.environ.get("YTDLP_PROXY", "").strip()
        ordered = ([private_proxy] if private_proxy else []) + selected
        if not ordered:
            print("No proxy passed the bounded YouTube HTTPS probes; direct downloads remain enabled.")
        write_github_env("YTDLP_PROXY_POOL_JSON", json.dumps(ordered), env_file)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1

    source_summary = ", ".join(
        f"{source}: {count}" for source, count in source_counts.items()
    )
    print(
        f"Proxy lists checked: {len(candidates)}; candidate cap: "
        f"{MAX_CANDIDATES_PER_SOURCE} per source; selected public proxies: "
        f"{len(selected)}; top-proxy source counts: {source_summary}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
