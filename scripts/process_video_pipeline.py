#!/usr/bin/env python3
"""End-to-end orchestration for a single YouTube video: monitor → download → burn → publish.

Glues the repository's existing departments into one linear pipeline:

    1. monitor   - fetch metadata via yt-dlp, verify the video ID, skip if already
                   recorded in the JSONL processing ledger.
    2. download  - run scripts/download_youtube_bilingual_srt.py to fetch the two
                   subtitle tracks (zh-Hans + en-orig), the source video, and build
                   the precise bilingual ASS.
    3. burn      - the downloader burns the ASS into the MP4 (FFmpeg libx264) and
                   writes BURN.json / TIMING.json.
    4. publish   - generate Pages archive pages, upload the burned MP4 to a GitHub
                   Release, and append the record to the JSONL ledger.

Usage::

    python scripts/process_video_pipeline.py \
        --url https://www.youtube.com/watch?v=lnOiG5dI4hg \
        --output-dir output \
        --state-file .github/state/whitehouse-processed-videos.txt

Exit codes: 0 on full success, 1 on any validation/processing/publishing failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DOWNLOADER = SCRIPTS / "download_youtube_bilingual_srt.py"
PAGE_GEN = SCRIPTS / "generate_video_pages.py"

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
DEFAULT_LANGUAGES = "zh-Hans,en-orig"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def now_ns() -> tuple[int, str]:
    epoch_ns = time.time_ns()
    seconds, nanoseconds = divmod(epoch_ns, 1_000_000_000)
    iso = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return epoch_ns, f"{iso}.{nanoseconds:09d}Z"


def log(message: str) -> None:
    print(f"[{utc_now_iso()}] {message}", flush=True)


def run(command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    log("$ " + " ".join(str(part) for part in command))
    return subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
    )


def require_tool(name: str) -> None:
    result = run(["command", "-v", name])
    if result.returncode != 0 or not result.stdout.strip():
        print(f"Error: required tool '{name}' is not on PATH.", file=sys.stderr)
        sys.exit(1)


def video_id_from_url(url: str) -> str:
    """Extract an 11-character YouTube video ID from a URL or bare ID."""
    if VIDEO_ID_RE.fullmatch(url.strip()):
        return url.strip()
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host == "youtu.be" or host.endswith(".youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            parts = parsed.path.split("/")
            video_id = parts[2] if len(parts) > 2 else ""
        else:
            video_id = ""
    else:
        video_id = ""
    if not VIDEO_ID_RE.fullmatch(video_id or ""):
        raise ValueError(f"Not a supported YouTube video URL or ID: {url}")
    return video_id
def fetch_metadata(video_id: str, proxy: str | None = None) -> dict:
    """Return yt-dlp single-JSON metadata for the video.

    ``proxy`` is an optional ``http://host:port`` string passed to yt-dlp via
    ``--proxy`` when the direct connection to YouTube is blocked.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--skip-download",
        "--dump-single-json",
    ]
    if proxy:
        cmd.extend(["--proxy", proxy])
    cmd.append(url)
    proc = run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed to fetch metadata for {video_id}: {proc.stderr.strip()}"
        )
    try:
        metadata = json.loads(proc.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"yt-dlp metadata is not valid JSON: {error}")
    if not isinstance(metadata, dict) or metadata.get("id") != video_id:
        raise RuntimeError(f"YouTube metadata does not match requested video ID {video_id}.")
    return metadata


def load_processed_ids(state_file: Path) -> set[str]:
    """Return the set of YouTube source IDs already recorded in the ledger."""
    processed: set[str] = set()
    if not state_file.exists():
        return processed
    for line in state_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("source_platform") == "youtube":
            sid = record.get("source_id")
            if sid:
                processed.add(str(sid))
    return processed


def run_downloader(
    *,
    video_id: str,
    output_dir: Path,
    languages: str,
    style_preset: str,
    burn_in: bool,
) -> Path:
    """Run the bilingual subtitle downloader (+ optional burn-in).

    Returns the archive folder created by the downloader.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        "python",
        str(DOWNLOADER),
        url,
        "--output-dir",
        str(output_dir),
        "--languages",
        languages,
        "--style-preset",
        style_preset,
        "--download-source-video",
    ]
    if burn_in:
        cmd.append("--burn-in")
    proc = run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        print(proc.stdout, end="")
        print(proc.stderr, end="", file=sys.stderr)
        raise RuntimeError(
            f"download_youtube_bilingual_srt.py failed for {video_id} "
            f"(exit {proc.returncode})."
        )
    print(proc.stdout, end="")

    matches = sorted(
        [
            p
            for p in output_dir.iterdir()
            if p.is_dir() and p.name.endswith(f"_{video_id}")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        raise RuntimeError(f"The downloader did not create an archive folder for {video_id}.")
    return matches[0]


def run_page_gen() -> None:
    proc = run(["python", str(PAGE_GEN)], cwd=ROOT)
    if proc.returncode != 0:
        print(proc.stdout, end="")
        print(proc.stderr, end="", file=sys.stderr)
        raise RuntimeError(f"generate_video_pages.py failed (exit {proc.returncode}).")
    print(proc.stdout, end="")


def publish_release(
    *,
    video_id: str,
    video_title: str,
    burned_mp4: Path,
    release_owner: str,
    release_repo: str,
    token: str | None,
) -> str:
    """Create or update a GitHub Release and upload the burned MP4.

    Returns the release tag (video ID).
    """
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    repo = f"{release_owner}/{release_repo}"
    view = run(["gh", "release", "view", video_id, "--repo", repo], env=env)
    if view.returncode == 0:
        upload = run(
            [
                "gh",
                "release",
                "upload",
                video_id,
                str(burned_mp4),
                "--repo",
                repo,
                "--clobber",
            ],
            env=env,
        )
        if upload.returncode != 0:
            print(upload.stdout, end="")
            print(upload.stderr, end="", file=sys.stderr)
            raise RuntimeError(f"gh release upload failed for {video_id}.")
        log(f"Uploaded burned MP4 to existing release {video_id}.")
    else:
        create = run(
            [
                "gh",
                "release",
                "create",
                video_id,
                str(burned_mp4),
                "--repo",
                repo,
                "--title",
                video_title or video_id,
                "--notes",
                (
                    f"Automatically archived from YouTube with Chinese and English "
                    f"subtitles burned in. Source: https://www.youtube.com/watch?v={video_id}"
                ),
            ],
            env=env,
        )
        if create.returncode != 0:
            print(create.stdout, end="")
            print(create.stderr, end="", file=sys.stderr)
            raise RuntimeError(f"gh release create failed for {video_id}.")
        log(f"Created release {video_id} and uploaded burned MP4.")
    return video_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_ledger(
    *,
    state_file: Path,
    video_id: str,
    video_title: str,
    channel: str,
    channel_id: str,
    published_at: str | None,
    published_date: str | None,
    published_epoch_ns: int | None,
    time_source: str | None,
    time_precision: str,
    burned_mp4: Path,
) -> None:
    """Append (or replace) a JSONL record in the processing ledger."""
    _, recorded_at = now_ns()
    repo = os.environ.get("GITHUB_REPOSITORY", "gitsynonyms/gitsynonyms")
    artifacts = [
        {
            "type": "captioned_video_release",
            "url": f"https://github.com/{repo}/releases/tag/{video_id}",
        },
    ]
    if burned_mp4 and burned_mp4.exists():
        artifacts.append(
            {
                "type": "burned_mp4",
                "path": str(burned_mp4),
                "sha256": sha256_file(burned_mp4),
            }
        )
    record = {
        "schema_version": 1,
        "record_type": "source_archive",
        "source_platform": "youtube",
        "source_id": video_id,
        "source_channel_id": channel_id or None,
        "source_channel": channel or None,
        "title": video_title or None,
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
        "published_at": published_at or None,
        "published_epoch_ns": published_epoch_ns,
        "published_date": published_date or None,
        "published_time_source": time_source or None,
        "published_time_precision": time_precision,
        "recorded_at": recorded_at,
        "processing_status": "published",
        "artifacts": artifacts,
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if state_file.exists():
        for line in state_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                existing.append({"_raw": line})
    replaced = False
    for index, item in enumerate(existing):
        if isinstance(item, dict) and item.get("source_id") == video_id:
            existing[index] = record
            replaced = True
            break
    if not replaced:
        existing.append(record)
    state_file.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in existing
        ),
        encoding="utf-8",
        newline="\n",
    )
    log(f"Appended ledger record for {video_id} to {state_file}.")

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Process a single YouTube video end to end: monitor metadata, "
            "download bilingual subtitles, burn them into the video, and publish."
        )
    )
    parser.add_argument(
        "--url",
        required=True,
        help="YouTube watch URL or 11-character video ID.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "output"),
        help="Subtitle/archive root (default: <repo>/output).",
    )
    parser.add_argument(
        "--state-file",
        default=str(ROOT / ".github" / "state" / "whitehouse-processed-videos.txt"),
        help="JSONL processing ledger path.",
    )
    parser.add_argument(
        "--languages",
        default=DEFAULT_LANGUAGES,
        help="Two comma-separated YouTube language codes (default: zh-Hans,en-orig).",
    )
    parser.add_argument(
        "--style-preset",
        choices=("classic", "cinema"),
        default="classic",
        help="ASS style preset (default: classic).",
    )
    parser.add_argument(
        "--no-burn-in",
        action="store_true",
        help="Skip burning subtitles into the MP4 (download only).",
    )
    parser.add_argument(
        "--no-pages",
        action="store_true",
        help="Skip generating GitHub Pages archive pages.",
    )
    parser.add_argument(
        "--no-release",
        action="store_true",
        help="Skip creating/updating the GitHub Release upload.",
    )
    parser.add_argument(
        "--release-owner",
        default="gitsynonyms",
        help="GitHub release repository owner (default: gitsynonyms).",
    )
    parser.add_argument(
        "--release-repo",
        default="gitsynonyms",
        help="GitHub release repository name (default: gitsynonyms).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process even if the video is already recorded in the ledger.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    require_tool("yt-dlp")
    require_tool("gh")

    try:
        video_id = video_id_from_url(args.url)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_file = Path(args.state_file)

    # ------------------------------------------------------------------
    # Step 1/4: Monitor - fetch metadata and check ledger.
    # ------------------------------------------------------------------
    proxy = os.environ.get("YTDLP_PROXY", "").strip() or None
    if proxy:
        log(f"Using YTDLP_PROXY for YouTube access: {proxy}")
    try:
        metadata = fetch_metadata(video_id, proxy=proxy)
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    video_title = str(metadata.get("title") or video_id)
    channel = str(metadata.get("channel") or metadata.get("uploader") or "")
    channel_id = str(metadata.get("channel_id") or "")
    duration = metadata.get("duration")
    upload_date = str(metadata.get("upload_date") or "")
    published_date = ""
    if upload_date and len(upload_date) == 8:
        try:
            published_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        except ValueError:
            published_date = ""
    timestamp = metadata.get("timestamp")
    published_at = None
    time_source = ""
    time_precision = "unknown"
    if isinstance(timestamp, (int, float)):
        try:
            moment = datetime.fromtimestamp(float(timestamp), timezone.utc)
            published_at = moment.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
            time_source = "timestamp"
            time_precision = "second"
        except (OSError, OverflowError, ValueError):
            published_at = None

    log(
        f"Monitor: {video_id} | {video_title} | channel={channel} ({channel_id}) | "
        f"duration={duration}s | upload_date={upload_date}"
    )

    processed_ids = load_processed_ids(state_file)
    if video_id in processed_ids and not args.force:
        print(
            f"Video {video_id} is already recorded in the ledger; use --force to re-process.",
            file=sys.stderr,
        )
        return 0

    # ------------------------------------------------------------------
    # Step 2/4 + 3/4: Download + burn.
    # ------------------------------------------------------------------
    try:
        folder = run_downloader(
            video_id=video_id,
            output_dir=output_dir,
            languages=args.languages,
            style_preset=args.style_preset,
            burn_in=not args.no_burn_in,
        )
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    burned_mp4 = None
    if not args.no_burn_in:
        candidates = sorted(folder.glob(f"Burned_Bilingual_{video_id}.mp4"))
        if not candidates:
            candidates = sorted(output_dir.rglob(f"Burned_Bilingual_{video_id}.mp4"))
        if not candidates:
            print(
                f"Error: burn-in did not produce Burned_Bilingual_{video_id}.mp4 in {folder}.",
                file=sys.stderr,
            )
            return 1
        burned_mp4 = candidates[0]
        log(f"Burned MP4: {burned_mp4} ({burned_mp4.stat().st_size} bytes).")

    # ------------------------------------------------------------------
    # Step 4/4: Publish.
    # ------------------------------------------------------------------
    published_epoch_ns = None
    if published_at:
        try:
            moment = datetime.strptime(
                published_at, "%Y-%m-%dT%H:%M:%S.000000000Z"
            ).replace(tzinfo=timezone.utc)
            published_epoch_ns = int(moment.timestamp() * 1_000_000_000)
        except ValueError:
            published_epoch_ns = None

    if not args.no_pages:
        try:
            run_page_gen()
            log("Generated GitHub Pages archive pages.")
        except RuntimeError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    if burned_mp4 is not None and not args.no_release:
        try:
            publish_release(
                video_id=video_id,
                video_title=video_title,
                burned_mp4=burned_mp4,
                release_owner=args.release_owner,
                release_repo=args.release_repo,
                token=os.environ.get("GH_TOKEN"),
            )
        except RuntimeError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    try:
        append_ledger(
            state_file=state_file,
            video_id=video_id,
            video_title=video_title,
            channel=channel,
            channel_id=channel_id,
            published_at=published_at,
            published_date=published_date or None,
            published_epoch_ns=published_epoch_ns,
            time_source=time_source,
            time_precision=time_precision,
            burned_mp4=burned_mp4 or Path(""),
        )
    except OSError as error:
        print(f"Error: failed to append ledger record: {error}", file=sys.stderr)
        return 1

    log(f"End-to-end pipeline complete for {video_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

