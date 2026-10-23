#!/usr/bin/env python3
"""Find the newest unprocessed upload on the verified White House YouTube channels."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

CHANNEL_ID = "UCYxRlFDqcWM4y7FfpiAN3KQ"
CHANNELS = (
    "https://www.youtube.com/@WhiteHouse/videos",
    "https://www.youtube.com/@WhiteHouse/streams",
)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
ROOT = Path(__file__).resolve().parents[2]


def processed_video_ids(path: Path) -> set[str]:
    processed: set[str] = set()
    if not path.is_file():
        return processed
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid processed-video record in {path}.") from error
            if not isinstance(record, dict):
                raise ValueError(f"Invalid processed-video record in {path}.")
            if record.get("source_platform") == "youtube":
                candidate = str(record.get("source_id") or "")
                if VIDEO_ID.fullmatch(candidate):
                    processed.add(candidate)
        elif VIDEO_ID.fullmatch(line):
            processed.add(line)
    return processed


def proxy_candidates(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid proxy index in {path}.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("top", []), list):
        raise ValueError(f"Invalid proxy index structure in {path}.")
    candidates = []
    for entry in payload.get("top", []):
        candidate = entry.get("proxy") if isinstance(entry, dict) else entry
        if isinstance(candidate, str) and candidate and candidate not in candidates:
            candidates.append(candidate)
        if len(candidates) == 3:
            break
    return candidates


def run_ytdlp(
    args: Sequence[str],
    proxies: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    failures = []
    for proxy in (None, *proxies):
        command = ["yt-dlp", "--js-runtimes", "deno", "--no-warnings"]
        if proxy:
            command.extend(["--proxy", proxy])
        completed = runner(
            [*command, *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout
        details = (completed.stderr or "").strip()
        failures.append(details[:300] or f"yt-dlp exited with code {completed.returncode}")
    raise RuntimeError("yt-dlp failed directly and through configured proxies: " + " | ".join(failures))


def choose_video(
    video_ids: Sequence[str],
    processed: set[str],
    load_metadata: Callable[[str], dict],
) -> dict | None:
    for candidate in video_ids:
        if not VIDEO_ID.fullmatch(candidate) or candidate in processed:
            continue
        metadata = load_metadata(candidate)
        if metadata.get("id") != candidate:
            raise ValueError(f"yt-dlp returned metadata for a different video than {candidate}.")
        if metadata.get("channel_id") != CHANNEL_ID:
            raise ValueError(f"Video {candidate} is not from the verified White House channel.")
        return metadata
    return None


def discover_video(
    *,
    state_path: Path,
    proxy_index: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict | None:
    proxies = proxy_candidates(proxy_index)
    ids: list[str] = []
    for channel in CHANNELS:
        output = run_ytdlp(
            [
                "--ignore-errors",
                "--flat-playlist",
                "--playlist-end",
                "10",
                "--print",
                "%(id)s",
                channel,
            ],
            proxies,
            runner=runner,
        )
        channel_ids = [
            line.strip()
            for line in output.splitlines()
            if VIDEO_ID.fullmatch(line.strip())
        ]
        ids.extend(channel_ids)

    if not ids:
        raise RuntimeError("No video IDs were returned by either White House channel listing.")

    processed = processed_video_ids(state_path)

    def load_metadata(video_id: str) -> dict:
        output = run_ytdlp(
            [
                "--skip-download",
                "--dump-single-json",
                "--no-playlist",
                f"https://www.youtube.com/watch?v={video_id}",
            ],
            proxies,
            runner=runner,
        )
        try:
            metadata = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"yt-dlp returned invalid metadata for {video_id}.") from error
        if not isinstance(metadata, dict):
            raise RuntimeError(f"yt-dlp returned invalid metadata for {video_id}.")
        return metadata

    return choose_video(ids, processed, load_metadata)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-json",
        type=Path,
        required=True,
        help="Write the discovered video URL as JSON, or an empty video_url when none is new.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=ROOT / ".github" / "state" / "whitehouse-processed-videos.txt",
    )
    parser.add_argument(
        "--proxy-index",
        type=Path,
        default=ROOT / "proxyindex" / "top10.json",
    )
    args = parser.parse_args(argv)
    try:
        metadata = discover_video(state_path=args.state_file, proxy_index=args.proxy_index)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    video_url = ""
    if metadata:
        video_url = f"https://www.youtube.com/watch?v={metadata['id']}"
    args.output_json.write_text(
        json.dumps({"video_url": video_url}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
