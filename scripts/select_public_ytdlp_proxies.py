#!/usr/bin/env python3
"""Pick the fastest verified proxy from the full list² scan for yt-dlp.

The selector no longer samples three hand-picked lists.  It consumes the
published result of ``proxyindex/full_scan.py`` -- which enumerates the whole
Chinese/English query matrix, expands every repository x file pattern source,
probes every candidate and publishes a top ten -- and re-probes those candidates
against the exact YouTube video that is about to be downloaded.  When no fresh
scan exists it runs one (bounded by a time budget) before selecting.
"""

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
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path


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
INDEX_DIR = Path(__file__).resolve().parents[1] / "proxyindex"
FULL_SCAN_SCRIPT = INDEX_DIR / "full_scan.py"
FULL_SCAN_TTL_SECONDS = 3600.0
FULL_SCAN_BUDGET_SECONDS = 900.0
MAX_INDEX_CANDIDATES = 40
MAX_WORKERS = 15
MAX_PLAYER_RESPONSE_BYTES = 4_000_000
IP_PORT_PATTERN = re.compile(r"^(?:https?://)?(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})/?$")
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


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


def youtube_video_id(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("PROBE_VIDEO_URL must be an absolute YouTube URL.")
    host = (parsed.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        raise ValueError("PROBE_VIDEO_URL must use a supported YouTube host.")
    if host == "youtu.be":
        video_id = parsed.path.strip("/")
    elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
        video_id = parsed.path.split("/")[2]
    else:
        video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError("PROBE_VIDEO_URL must contain a valid YouTube video ID.")
    return video_id


def player_page_is_available(content: bytes, video_id: str) -> bool:
    page = content.decode("utf-8", errors="replace")
    response_marker = page.find("ytInitialPlayerResponse")
    if response_marker < 0:
        return False
    response_start = page.find("{", response_marker)
    if response_start < 0:
        return False
    try:
        player_response, _ = json.JSONDecoder().raw_decode(page[response_start:])
    except json.JSONDecodeError:
        return False
    return (
        player_response.get("playabilityStatus", {}).get("status") == "OK"
        and player_response.get("videoDetails", {}).get("videoId") == video_id
    )


def probe_proxy(proxy: str, video_id: str | None = None) -> float | None:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
    probe_url = (
        f"https://www.youtube.com/watch?v={video_id}"
        if video_id
        else PROBE_URL
    )
    request = urllib.request.Request(
        probe_url,
        headers={"User-Agent": "gitsynonyms-youtube-proxy-check/1.0"},
    )
    started = time.monotonic()
    try:
        with opener.open(
            request,
            timeout=PROBE_TIMEOUT_SECONDS * (2 if video_id else 1),
        ) as response:
            if video_id:
                content = response.read(MAX_PLAYER_RESPONSE_BYTES)
                if response.status != 200 or not player_page_is_available(content, video_id):
                    return None
            elif response.status != 204:
                return None
    except (OSError, urllib.error.URLError, http.client.HTTPException, TimeoutError):
        return None
    return time.monotonic() - started


def select_proxies(
    candidates_by_source: dict[str, list[str]],
    check_proxy: Callable[[str], float | None] | None = None,
    *,
    video_id: str | None = None,
) -> tuple[list[str], dict[str, int]]:
    candidate_sources: dict[str, set[str]] = {}
    for source, candidates in candidates_by_source.items():
        for proxy in candidates:
            candidate_sources.setdefault(proxy, set()).add(source)

    results: list[tuple[float, str]] = []
    probe = check_proxy or (
        lambda proxy: probe_proxy(proxy, video_id=video_id)
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(probe, proxy): proxy
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


def index_scan_is_fresh(index_dir: Path, ttl_seconds: float, now: datetime | None = None) -> bool:
    try:
        state = json.loads((index_dir / "scan-state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    try:
        finished = datetime.fromisoformat(
            str(state.get("finished_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return False
    moment = now or datetime.now(timezone.utc)
    return (moment - finished).total_seconds() <= max(0.0, ttl_seconds)


def load_index_candidates(
    index_dir: Path = INDEX_DIR, limit: int = MAX_INDEX_CANDIDATES
) -> list[str]:
    """Candidates from the published full scan, fastest first."""
    candidates: list[str] = []
    try:
        payload = json.loads((index_dir / "top10.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    entries = payload.get("top") or []
    for entry in entries:
        value = entry.get("proxy") if isinstance(entry, dict) else entry
        normalized = normalize_candidate(str(value or ""))
        if normalized and normalized not in candidates:
            candidates.append(normalized)
        if len(candidates) >= limit:
            return candidates
    if candidates:
        return candidates
    for line in _read_index_lines(index_dir / "alive_http.txt"):
        normalized = normalize_candidate(line)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
        if len(candidates) >= limit:
            break
    return candidates


def _read_index_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def ensure_full_scan(
    *,
    index_dir: Path = INDEX_DIR,
    mode: str = "full",
    ttl_seconds: float = FULL_SCAN_TTL_SECONDS,
    budget_seconds: float = FULL_SCAN_BUDGET_SECONDS,
    runner=None,
    now: datetime | None = None,
) -> dict:
    """Run the fixed full-scan flow unless a fresh result already exists."""
    result = {"mode": mode, "ran": False, "status": "cached", "detail": ""}
    if mode == "off":
        result["status"] = "off"
        return result
    if mode == "auto" and index_scan_is_fresh(index_dir, ttl_seconds, now=now):
        return result
    script = index_dir / "full_scan.py"
    if not script.is_file():
        result.update(status="unavailable", detail=f"missing {script}")
        return result
    command = [
        sys.executable,
        str(script),
        "scan",
        "--index-dir",
        str(index_dir),
        "--budget-seconds",
        str(budget_seconds),
        "--quiet",
    ]
    (runner or _run_scan_command)(command)
    result.update(ran=True, status="scanned")
    return result


def _run_scan_command(command: list[str]) -> None:
    import subprocess

    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=None
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip()[-300:])


def main() -> int:
    env_file = os.environ.get("GITHUB_ENV")
    if not env_file:
        print("GITHUB_ENV is not set; run this selector inside GitHub Actions.", file=sys.stderr)
        return 1
    try:
        scan_mode = os.environ.get("PROXY_SCAN_MODE", "full").strip().lower()
        scan_budget = float(os.environ.get("PROXY_SCAN_BUDGET", FULL_SCAN_BUDGET_SECONDS))
        index_dir = Path(os.environ.get("PROXY_INDEX_DIR", INDEX_DIR))
        scan = ensure_full_scan(
            index_dir=index_dir,
            mode="auto" if scan_mode in {"auto", "cached"} else scan_mode,
            budget_seconds=scan_budget,
        )
        candidates = load_index_candidates(index_dir)
        if not candidates and scan_mode != "bounded":
            print(
                "::warning::the full scan published no verified proxy; "
                "falling back to the bounded public lists",
                file=sys.stderr,
            )
            candidates = fetch_candidates()
        if not candidates:
            candidates = fetch_candidates()
        probe_url = os.environ.get("PROBE_VIDEO_URL", "").strip()
        video_id = youtube_video_id(probe_url) if probe_url else None
        selected, source_counts = select_proxies(candidates, video_id=video_id)
        private_proxy = os.environ.get("YTDLP_PROXY", "").strip()
        ordered = ([private_proxy] if private_proxy else []) + selected
        if not ordered:
            print("No proxy passed the YouTube HTTPS probes; direct downloads remain enabled.")
        write_github_env("YTDLP_PROXY_POOL_JSON", json.dumps(ordered), env_file)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1

    source_summary = ", ".join(
        f"{source}: {count}" for source, count in source_counts.items()
    )
    print(
        f"Proxy scan: {scan['status']}"
        f"{' (ran)' if scan.get('ran') else ''}; candidates checked: "
        f"{len(candidates)}; selected public proxies: {len(selected)}; probe target: "
        f"{video_id or 'generic YouTube connectivity'}; "
        f"top-proxy source counts: {source_summary}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
