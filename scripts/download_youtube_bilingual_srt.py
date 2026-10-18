#!/usr/bin/env python3
"""Download YouTube English and Chinese captions and merge them into one SRT."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import parse_qs, urlparse


def video_id_from_url(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be" or host.endswith(".youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            video_id = parsed.path.split("/")[2]
        else:
            video_id = ""
    else:
        video_id = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise ValueError(f"Not a supported YouTube video URL or ID: {value}")
    return video_id


def timestamp_to_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    hours_value, minutes_value, seconds_value = int(hours), int(minutes), float(seconds)
    if minutes_value > 59 or seconds_value >= 60:
        raise ValueError(f"Invalid SRT timestamp: {value!r}")
    result = hours_value * 3600 + minutes_value * 60 + seconds_value
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Invalid SRT timestamp: {value!r}")
    return result


def parse_srt(content: str) -> list[dict]:
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    timestamp = r"\d{2}:\d{2}:\d{2}[,.]\d{3}"
    cue_start = re.compile(
        rf"(?m)^[ \t]*(\d+)[ \t]*\n({timestamp})[ \t]*-->[ \t]*"
        rf"({timestamp})(?:[ \t]+[^\n]*)?[ \t]*\n"
    )
    headers = list(cue_start.finditer(normalized))
    if not headers:
        raise ValueError("Subtitle file has no numbered SRT cues.")
    result = []
    for index, match in enumerate(headers):
        body_end = headers[index + 1].start() if index + 1 < len(headers) else len(normalized)
        start = timestamp_to_seconds(match.group(2))
        end = timestamp_to_seconds(match.group(3))
        if end <= start:
            raise ValueError(f"SRT cue end must be after its start: {match.group(0).strip()!r}")
        lines = normalized[match.end() : body_end].split("\n")
        while lines and lines[0] == "":
            lines.pop(0)
        while lines and lines[-1] == "":
            lines.pop()
        text = "\n".join(lines)
        if text.strip():
            result.append(
                {
                    "start": start,
                    "end": end,
                    "start_timestamp": match.group(2),
                    "end_timestamp": match.group(3),
                    "text": text,
                }
            )
    if not result:
        raise ValueError("Subtitle file is empty or not valid SRT.")
    return result


def align_and_merge(
    first: Sequence[dict],
    second: Sequence[dict],
) -> str:
    if not first or not second:
        raise ValueError("Both subtitle tracks must contain cues.")
    cues = [*first, *second]
    if any(cue["end"] <= cue["start"] or not cue["text"].strip() for cue in cues):
        raise ValueError("Cannot serialize empty or invalid subtitle cues.")

    boundaries = sorted({cue[key] for cue in cues for key in ("start", "end")})
    segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        top_cues = sorted(
            (cue for cue in first if cue["start"] < end and cue["end"] > start),
            key=lambda cue: cue["start"],
        )
        bottom_cues = sorted(
            (cue for cue in second if cue["start"] < end and cue["end"] > start),
            key=lambda cue: cue["start"],
        )
        top = "\n".join(cue["text"] for cue in top_cues)
        bottom = "\n".join(cue["text"] for cue in bottom_cues)
        if not top and not bottom:
            continue
        if segments and segments[-1]["end"] == start and (
            segments[-1]["top"], segments[-1]["bottom"]
        ) == (top, bottom):
            segments[-1]["end"] = end
        else:
            segments.append({"start": start, "end": end, "top": top, "bottom": bottom})

    blocks = []
    for index, segment in enumerate(segments, 1):
        start = format_timestamp(segment["start"])
        end = format_timestamp(segment["end"])
        text = "\n".join(line for line in (segment["top"], segment["bottom"]) if line)
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n"


def format_timestamp(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"Invalid subtitle time: {seconds!r}")
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def validate_srt(content: str) -> int:
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n\s*\n", normalized)
    timing = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")
    for expected_number, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(expected_number) or not timing.fullmatch(lines[1]):
            raise ValueError(f"Generated SRT cue {expected_number} is not valid SubRip.")
        start_text, end_text = lines[1].split(" --> ")
        if timestamp_to_seconds(end_text) <= timestamp_to_seconds(start_text):
            raise ValueError(f"Generated SRT cue {expected_number} has an invalid time range.")
        if not any(line.strip() for line in lines[2:]):
            raise ValueError(f"Generated SRT cue {expected_number} has no caption text.")
    return len(blocks)


def write_srt(path: Path, content: str) -> None:
    cue_count = validate_srt(content)
    normalized = content.rstrip("\r\n").replace("\r\n", "\n").replace("\r", "\n")
    path.write_text(normalized.replace("\n", "\r\n") + "\r\n", encoding="utf-8-sig", newline="")
    written = path.read_text(encoding="utf-8-sig")
    if validate_srt(written) != cue_count:
        raise OSError(f"Subtitle output failed validation after writing: {path}")


def pick_language(tracks: dict, preferences: Sequence[str]) -> str | None:
    lowered = {code.lower(): code for code in tracks}
    return next((lowered[code.lower()] for code in preferences if code.lower() in lowered), None)


def classify_track(language: str, metadata: dict) -> str:
    if pick_language(metadata.get("subtitles", {}), (language,)):
        return "manual"
    if not pick_language(metadata.get("automatic_captions", {}), (language,)):
        raise RuntimeError(f"YouTube does not list a subtitle track for language {language!r}.")
    source_language = str(metadata.get("language") or "").lower().split("-")[0]
    track_language = language.lower().split("-")[0]
    return "auto-generated" if source_language and track_language == source_language else "auto-translated"


def subtitle_wait(language: str, chinese_wait: int) -> int:
    return chinese_wait if language.lower().split("-")[0] == "zh" else 0


def run_ytdlp(
    args: Sequence[str], *, show_progress: bool = False
) -> subprocess.CompletedProcess:
    executable = shutil.which("yt-dlp")
    if not executable:
        raise RuntimeError("yt-dlp is not installed or is not available on PATH.")
    try:
        return subprocess.run(
            [executable, *args],
            check=True,
            capture_output=not show_progress,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or str(error)).strip()
        message = f"yt-dlp failed: {details}" if details else f"yt-dlp exited with code {error.returncode}."
        raise RuntimeError(message) from error


def download_track(
    url: str, video_id: str, language: str, track_kind: str, directory: Path, wait: int
) -> tuple[Path, list[dict]]:
    print(
        f"  Requesting {language} ({track_kind}); yt-dlp will wait {wait}s before the request.",
        flush=True,
    )
    args = [
        "--no-warnings",
        "--skip-download",
        "--retries",
        "5",
        "--no-overwrites",
    ]
    if wait:
        args.extend(["--sleep-subtitles", str(wait)])
    args.extend([
        "--write-subs" if track_kind == "manual" else "--write-auto-subs",
        "--sub-langs",
        language,
        "--sub-format",
        "srt/best",
        "--convert-subs",
        "srt",
        "-o",
        str(directory / "%(id)s.%(language)s.%(ext)s"),
        url,
    ])
    run_ytdlp(args, show_progress=True)
    matches = [
        path
        for path in directory.glob(f"{video_id}.*")
        if path.is_file() and path.name.lower().endswith(f".{language.lower()}.srt")
    ]
    if not matches:
        raise RuntimeError(f"yt-dlp did not produce the requested {language} SRT.")
    source_path = matches[0]
    return source_path, parse_srt(source_path.read_text(encoding="utf-8-sig"))


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_")[:90] or "youtube-video"


def suggested_date(title: str, upload_date: str) -> str:
    match = re.search(r"([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})", title)
    if match:
        for fmt in ("%b. %d, %Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(match.group(1), fmt).date().isoformat()
            except ValueError:
                continue
    if re.fullmatch(r"\d{8}", upload_date or ""):
        return datetime.strptime(upload_date, "%Y%m%d").date().isoformat()
    return date.today().isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download two YouTube subtitle tracks and merge them into one bilingual SRT."
    )
    parser.add_argument("url", help="YouTube URL or 11-character video ID.")
    parser.add_argument("--output-dir", default="output", help="Subtitle archive root (default: output).")
    parser.add_argument("--event", help="Override the event/title used in the folder name.")
    parser.add_argument("--date", help="Override event date (YYYY-MM-DD).")
    parser.add_argument(
        "--languages",
        default="zh-Hans,en-orig",
        help="Two comma-separated YouTube language codes, in output order (default: zh-Hans,en-orig).",
    )
    parser.add_argument(
        "--sleep-subtitles",
        type=int,
        default=56,
        help="Wait before requesting Chinese subtitles (default: 56 seconds; English is not delayed).",
    )
    args = parser.parse_args(argv)
    if args.sleep_subtitles < 0:
        parser.error("--sleep-subtitles must not be negative")
    languages = [language.strip() for language in args.languages.split(",")]
    if len(languages) != 2 or not all(languages) or languages[0].lower() == languages[1].lower():
        parser.error("--languages must contain two distinct comma-separated language codes")

    try:
        print("Step 1/5: Inspecting video and available subtitle tracks...", flush=True)
        video_id = video_id_from_url(args.url)
        metadata = json.loads(
            run_ytdlp(["--no-warnings", "--skip-download", "--dump-single-json", args.url]).stdout
        )
        manual = metadata.get("subtitles", {})
        automatic = metadata.get("automatic_captions", {})
        tracks = {**automatic, **manual}
        first_code = pick_language(tracks, (languages[0],))
        second_code = pick_language(tracks, (languages[1],))
        if first_code is None:
            raise RuntimeError(f"YouTube does not list a subtitle track for language {languages[0]!r}.")
        if second_code is None:
            raise RuntimeError(f"YouTube does not list a subtitle track for language {languages[1]!r}.")
        first_kind = classify_track(first_code, metadata)
        second_kind = classify_track(second_code, metadata)
        print(
            f"  Video: {metadata.get('title') or video_id}\n"
            f"  Subtitle order: {first_code} above {second_code}",
            flush=True,
        )

        title = args.event or str(metadata.get("title") or video_id)
        event_date = args.date or suggested_date(title, str(metadata.get("upload_date", "")))
        try:
            event_date = date.fromisoformat(event_date).isoformat()
        except ValueError as error:
            raise ValueError("--date must use YYYY-MM-DD format.") from error
        folder = Path(args.output_dir) / f"{event_date}_{safe_name(title)}_{video_id}"
        folder.mkdir(parents=True, exist_ok=True)

        print("Step 2/5: Downloading bottom track ({}) first...".format(second_code), flush=True)
        second_path, second = download_track(
            args.url,
            video_id,
            second_code,
            second_kind,
            folder,
            subtitle_wait(second_code, args.sleep_subtitles),
        )
        print(f"Step 3/5: Downloading top track ({first_code})...", flush=True)
        first_path, first = download_track(
            args.url,
            video_id,
            first_code,
            first_kind,
            folder,
            subtitle_wait(first_code, args.sleep_subtitles),
        )

        print("Step 4/5: Merging the unchanged downloaded tracks...", flush=True)
        combined_kind = f"{first_kind}+{second_kind}"
        output = folder / f"{combined_kind}_Bilingual_{first_code}-{second_code}.srt"
        write_srt(output, align_and_merge(first, second))
        print("Step 5/5: Validating generated SRT files...", flush=True)
        cue_count = validate_srt(output.read_text(encoding="utf-8-sig"))
        print(f"  OK: {output.name} ({cue_count} cues)", flush=True)
        print("Original downloaded subtitles were left untouched:", flush=True)
        print(first_path.resolve())
        print(second_path.resolve())
        print("Done. Subtitle files:", flush=True)
        print(output.resolve())
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
