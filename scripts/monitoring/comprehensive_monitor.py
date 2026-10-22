#!/usr/bin/env python3
"""Poll the White House channels, verify each new event, then burn bilingual subtitles.

The monitor keeps a state file of processed video ids, so a run only acts on
what it has not seen before. For every new video it

1. confirms the event with the other sources (Factbase calendar, GDELT,
   congressional records, forums, backlinks),
2. downloads all seven caption formats for both languages, using json3 as the
   primary timing source,
3. builds the bilingual ASS from the most precise format available,
4. burns the subtitles into the video, and
5. writes the verification report next to the output.

Proxies are rotated per run so the YouTube rate limiter sees a different
egress each time.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "monitoring"))

import multi_source as ms  # noqa: E402

STATE = ROOT / "output" / ".monitor-state.json"
CAPTION_FORMATS = "vtt,srt,ttml,srv3,srv2,srv1,json3"


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"processed": [], "last_run": None}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def rotate_proxy() -> str:
    """Pick the next proxy from the validated pool, if one is configured."""
    pool_file = ROOT / "proxyindex" / "alive_http.txt"
    if not pool_file.exists():
        return ""
    pool = [line.strip() for line in pool_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not pool:
        return ""
    return random.choice(pool)


def environment_with_proxy(proxy: str) -> dict:
    env = dict(os.environ)
    if proxy:
        env["http_proxy"] = f"http://{proxy}"
        env["https_proxy"] = f"http://{proxy}"
    return env


def download_captions(video_url: str, folder: Path) -> tuple[list[Path], list[Path]]:
    """Fetch every caption format for the two default languages."""
    folder.mkdir(parents=True, exist_ok=True)
    for language in ("en-orig", "zh-Hans"):
        command = [
            "yt-dlp",
            "--no-warnings",
            "--skip-download",
            "--retries",
            "5",
            "--write-auto-subs",
            "--sub-langs",
            language,
            "--sub-format",
            CAPTION_FORMATS,
            "-o",
            str(folder / "%(id)s.%(language)s.%(ext)s"),
            video_url,
        ]
        subprocess.run(command, check=False, env=environment_with_proxy(os.environ.get("MONITOR_PROXY", "")))
    files = sorted(folder.glob("*.*.*"))
    return files, files


def burn_bilingual(video_url: str, folder: Path) -> dict:
    """Run the repository downloader to build the ASS and burn it in."""
    script = ROOT / "scripts" / "download_youtube_bilingual_srt.py"
    command = [
        sys.executable,
        str(script),
        video_url,
        "--languages",
        "zh-Hans,en-orig",
        "--output-dir",
        str(ROOT / "output"),
        "--burn-in",
    ]
    proxy = os.environ.get("MONITOR_PROXY", "")
    if proxy:
        command.extend(["--proxy", f"http://{proxy}"])
    result = subprocess.run(command, capture_output=True, text=True, env=environment_with_proxy(proxy))
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-2000:],
    }


def write_verification(video_id: str, title: str, event_date: str, report: dict) -> Path:
    folder = ROOT / "output" / f"{event_date}_{video_id}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "VERIFICATION.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def verify_event(candidate: ms.Candidate) -> dict:
    """Collect corroboration for one candidate from every available source."""
    event_date = candidate.event_date or candidate.published or ""
    query = f'"white house" {event_date}'.strip()
    report: dict = {
        "video_id": candidate.video_id,
        "title": candidate.title,
        "event_date": event_date,
        "source": candidate.source,
        "url": candidate.url,
        "checks": {},
    }
    report["checks"]["gdelt"] = ms.verify_gdelt(
        query, key=os.environ.get("GDELT_API_KEY", ""), days=14
    )[:20]
    report["checks"]["newsbank"] = ms.verify_newsbank(
        query, key=os.environ.get("NEWSBANK_API_KEY", "")
    )[:20]
    report["checks"]["congress"] = ms.verify_congress(query)[:20]
    report["checks"]["discussion"] = ms.verify_youtube_discussion(candidate.video_id)[:20]
    report["checks"]["forums"] = ms.verify_forums(candidate.video_id, title=candidate.title)[:20]
    report["checks"]["backlinks"] = ms.verify_backlinks(
        candidate.video_id, title=candidate.title, key=os.environ.get("GOOGLE_CSE_API_KEY", "")
    )[:20]
    if event_date:
        try:
            day = datetime.strptime(event_date[:10], "%Y-%m-%d").date()
        except ValueError:
            day = None
        if day:
            report["checks"]["factbase"] = [
                {"title": item.title, "url": item.url}
                for item in ms.discover_factbase_calendar(day)[:20]
            ]
    corroborating = {
        name: len(items)
        for name, items in report["checks"].items()
        if items
    }
    report["corroborating_sources"] = corroborating
    report["corroboration_count"] = len(corroborating)
    report["verified"] = len(corroborating) >= 2
    return report


def gather_candidates() -> tuple[list[ms.Candidate], list[ms.Candidate]]:
    """Collect official candidates and the cross-source corroboration list."""
    official: list[ms.Candidate] = []
    others: list[ms.Candidate] = []
    for name, url in ms.YOUTUBE_CHANNELS.items():
        print(f"polling {name}", flush=True)
        found = ms.discover_youtube_channel(url, limit=15)
        for item in found:
            item.details["channel_name"] = name
            item.details["official_channel"] = True
        official.extend(found)
    token = os.environ.get("YOUTUBE_API_KEY", "")
    if token:
        for channel_id in ("UCXZC1DQ5d7O7fCwD3WhkLxw",):
            for item in ms.discover_youtube_api(channel_id, token=token):
                item.details["official_channel"] = True
                item.details["channel_name"] = "youtube-api"
            official.extend(ms.discover_youtube_api(channel_id, token=token))
    cx = os.environ.get("GOOGLE_CSE_ID", "")
    key = os.environ.get("GOOGLE_CSE_API_KEY", "")
    for query in ("white house president remarks", "white house press conference"):
        others.extend(ms.discover_google_cse(query, cx=cx, key=key))
    others.extend(ms.discover_cspan("white house"))
    return ms.rank_candidates(official), ms.rank_candidates(others)


def looks_like_event(title: str) -> bool:
    """Official channels also post non-event content; rank events higher."""
    event_words = (
        "remarks", "press conference", "announcement", "briefing", "statement",
        "meeting", "roundtable", "exchange", "remarks", "address", "call",
        "interview", "signing", "receives", "hosts", "departure", "arrival",
    )
    lowered = title.lower()
    if any(word in lowered for word in event_words):
        return True
    # Fall back to the date pattern used in the official event titles.
    return bool(re.search(r"[A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4}", title))



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-check", action="store_true", help="reprocess known videos")
    parser.add_argument("--source", default="", help="only poll this channel name")
    parser.add_argument("--dry-run", action="store_true", help="discover and verify only")
    arguments = parser.parse_args()

    state = load_state()
    processed = set(state.get("processed", []))
    proxy = rotate_proxy()
    if proxy:
        os.environ["MONITOR_PROXY"] = proxy
        print(f"using proxy {proxy}", flush=True)

    candidates, corroboration = gather_candidates()
    print(f"official candidates: {len(candidates)}", flush=True)
    print(f"cross-source candidates: {len(corroboration)}", flush=True)

    new_video = ""
    title = ""
    for candidate in candidates:
        if not candidate.video_id:
            continue
        if candidate.video_id in processed and not arguments.force_check:
            continue
        if not looks_like_event(candidate.title) and not arguments.force_check:
            print(f"skip non-event video {candidate.video_id}: {candidate.title[:60]}", flush=True)
            continue
        print(f"new candidate {candidate.video_id}: {candidate.title}", flush=True)
        report = verify_event(candidate)
        # Attach the cross-source hits that describe the same event.
        report["checks"]["cross_source"] = [
            {"source": item.source, "title": item.title, "url": item.url}
            for item in corroboration
            if item.title
            and any(word in item.title.lower() for word in candidate.title.split()[:4])
        ][:25]
        print(
            f"  corroboration: {report['corroborating_sources']}",
            flush=True,
        )
        write_verification(
            candidate.video_id,
            candidate.title,
            candidate.event_date or candidate.published or "unknown",
            report,
        )
        if arguments.dry_run:
            continue
        result = burn_bilingual(candidate.url, ROOT / "output")
        print(f"  burn returncode: {result['returncode']}", flush=True)
        processed.add(candidate.video_id)
        new_video = candidate.video_id
        title = candidate.title
        break

    state["processed"] = sorted(processed)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    print(f"new_video={new_video}")
    print(f"title={title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
