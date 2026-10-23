#!/usr/bin/env python3
"""Select a working, unauthenticated HTTP proxy from public proxy lists."""

from __future__ import annotations

import concurrent.futures
import http.client
import ipaddress
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable


PROXY_LISTS = {
    "TheSpeedX": "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "monosans": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "proxifly": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
}
PROBE_URL = "https://www.youtube.com/generate_204"
FETCH_TIMEOUT_SECONDS = 12
PROBE_TIMEOUT_SECONDS = 4
MAX_CANDIDATES_PER_SOURCE = 30
MAX_WORKERS = 32
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


def fetch_lists(
    open_url: Callable[..., object] = urllib.request.urlopen,
) -> dict[str, list[str]]:
    available: dict[str, list[str]] = {}
    for source, url in PROXY_LISTS.items():
        try:
            with open_url(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
                content = response.read().decode("utf-8", errors="replace")
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            print(f"Warning: could not fetch {source} proxy list: {error}", file=sys.stderr)
            continue
        candidates = []
        seen = set()
        for line in content.splitlines():
            proxy = normalize_candidate(line)
            if proxy and proxy not in seen:
                seen.add(proxy)
                candidates.append(proxy)
            if len(candidates) >= MAX_CANDIDATES_PER_SOURCE:
                break
        if candidates:
            available[source] = candidates
        else:
            print(f"Warning: {source} proxy list contained no valid candidates.", file=sys.stderr)
    if len(available) < 2:
        raise RuntimeError(
            f"Only {len(available)} public proxy lists returned candidates; "
            "at least two sources are required."
        )
    return available


def probe_proxy(proxy: str) -> float | None:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
    request = urllib.request.Request(
        PROBE_URL,
        headers={"User-Agent": "gitsynonyms-public-proxy-check/1.0"},
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            if response.status != 204:
                return None
    except (OSError, urllib.error.URLError, http.client.HTTPException, TimeoutError):
        return None
    return time.monotonic() - started


def select_proxy(
    proxy_lists: dict[str, list[str]],
    check_proxy: Callable[[str], float | None] = probe_proxy,
) -> tuple[str, float, dict[str, int]]:
    provenance: dict[str, set[str]] = defaultdict(set)
    for source, proxies in proxy_lists.items():
        for proxy in proxies:
            provenance[proxy].add(source)
    candidates = sorted(provenance)
    results: list[tuple[float, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_proxy, proxy): proxy for proxy in candidates}
        for future in concurrent.futures.as_completed(futures):
            proxy = futures[future]
            try:
                latency = future.result()
            except Exception as error:
                raise RuntimeError(f"Proxy check failed unexpectedly for {proxy}: {error}") from error
            if latency is not None:
                results.append((latency, proxy))
    source_counts = {
        source: sum(source in provenance[proxy] for _, proxy in results)
        for source in proxy_lists
    }
    if not results:
        raise RuntimeError(
            f"No working public proxy passed the HTTPS YouTube probe across "
            f"{len(proxy_lists)} proxy-list sources."
        )
    latency, proxy = min(results)
    return proxy, latency, source_counts


def write_github_env(name: str, value: str, env_file: str) -> None:
    delimiter = f"GH_ENV_{uuid.uuid4().hex}"
    if "\n" in value or "\r" in value or delimiter in value:
        raise ValueError("Proxy URL contains unsupported line breaks.")
    with open(env_file, "a", encoding="utf-8", newline="\n") as output:
        output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main() -> int:
    env_file = os.environ.get("GITHUB_ENV")
    if not env_file:
        print("GITHUB_ENV is not set; run this selector inside GitHub Actions.", file=sys.stderr)
        return 1
    try:
        configured_proxy = os.environ.get("YTDLP_PROXY", "").strip()
        if configured_proxy:
            proxy = configured_proxy
            print("Using the configured YTDLP_PROXY secret; public proxy lookup skipped.")
            latency = None
            source_counts: dict[str, int] = {}
        else:
            proxy_lists = fetch_lists()
            proxy, latency, source_counts = select_proxy(proxy_lists)
        write_github_env("YTDLP_PROXY", proxy, env_file)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1

    if latency is None:
        print("Configured proxy is available to subsequent workflow steps.")
    else:
        sources = ", ".join(
            f"{source}: {count}" for source, count in source_counts.items()
        )
        print(
            f"Selected a public HTTP proxy that passed the YouTube HTTPS probe "
            f"({latency * 1000:.0f} ms); working candidates by source: {sources}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
