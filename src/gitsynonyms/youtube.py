from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, List, Sequence
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from deep_translator import GoogleTranslator
from deep_translator.exceptions import TooManyRequests
from youtube_transcript_api import YouTubeTranscriptApi


class SubtitleTranslationError(RuntimeError):
    pass


def extract_video_id(url: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url or ""):
        return url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    ):
        if parsed.path.startswith("/watch"):
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/", "/live/")):
            video_id = parsed.path.split("/")[2]
        else:
            video_id = ""
    elif host == "youtu.be" or host.endswith(".youtu.be"):
        video_id = parsed.path.strip("/").split("/")[0]
    else:
        video_id = ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return video_id
    raise ValueError(f"Unsupported YouTube URL or ID: {url!r}")


def fetch_transcript(
    video_url: str,
    language: str = "en",
    *,
    source: str = "yt-dlp",
) -> List[dict]:
    video_id = extract_video_id(video_url)
    if source == "transcript-api":
        transcript = YouTubeTranscriptApi().fetch(video_id, languages=[language])
        return [
            {"start": item.start, "duration": item.duration, "text": item.text}
            for item in transcript
        ]
    if source != "yt-dlp":
        raise ValueError("Transcript source must be 'yt-dlp' or 'transcript-api'.")

    metadata = json.loads(
        _run_yt_dlp(
            ["--no-warnings", "--skip-download", "--dump-single-json", video_url]
        ).stdout
    )
    manual = metadata.get("subtitles", {})
    automatic = metadata.get("automatic_captions", {})
    preferences = (
        ("en-orig", "en")
        if language.lower() == "en"
        else (language,)
    )
    track = _find_track(preferences, automatic)
    track_kind = "auto-generated"
    if track is None:
        track = _find_track(preferences, manual)
        track_kind = "manual"
    if track is None:
        raise SubtitleTranslationError(
            f"YouTube has no subtitle track matching {', '.join(preferences)}."
        )

    with TemporaryDirectory(prefix="youtube-transcript-") as temp:
        _, entries, _ = _download_yt_dlp_track(
            video_url, video_id, track, track_kind, Path(temp), sleep_seconds=0
        )
    return entries


def _format_timestamp(seconds: float) -> str:
    td = timedelta(seconds=seconds)
    total_ms = int((seconds - int(seconds)) * 1000)
    hours, remainder = divmod(int(td.total_seconds()), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{total_ms:03d}"


def build_srt(entries: Sequence[dict], *, chinese: Sequence[str] | None = None) -> str:
    if chinese is not None and len(chinese) != len(entries):
        raise ValueError("English and Chinese subtitle entries must have matching counts.")
    lines: List[str] = []
    for index, entry in enumerate(entries, start=1):
        start = float(entry.get("start", 0.0))
        duration = float(entry.get("duration", 0.0))
        text = str(entry.get("text", "")).strip()
        lines.append(str(index))
        lines.append(f"{_format_timestamp(start)} --> {_format_timestamp(start + duration)}")
        if chinese is not None:
            lines.append(str(chinese[index - 1]).strip())
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _without_rolling_cues(track: Sequence[dict]) -> list[dict]:
    ordered = sorted(track, key=lambda cue: float(cue["start"]))
    result = []
    for index, cue in enumerate(ordered):
        start = float(cue["start"])
        end = start + float(cue["duration"])
        if index + 1 < len(ordered):
            end = min(end, float(ordered[index + 1]["start"]))
        if end > start:
            result.append({**cue, "duration": end - start})
    return result


def _single_caption_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return text
    separator = "" if re.search(r"[\u2e80-\u9fff]", "".join(lines)) else " "
    return separator.join(lines)


def _pair_same_end_cues(
    top_track: Sequence[dict], bottom_track: Sequence[dict]
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    bottom_by_end: dict[int, list[tuple[int, dict]]] = {}
    for index, cue in enumerate(bottom_track):
        end = round((float(cue["start"]) + float(cue["duration"])) * 1000)
        bottom_by_end.setdefault(end, []).append((index, cue))

    pairs = []
    paired_top = set()
    paired_bottom = set()
    for top_index, cue in enumerate(top_track):
        end = round((float(cue["start"]) + float(cue["duration"])) * 1000)
        candidates = [
            item for item in bottom_by_end.get(end, []) if item[0] not in paired_bottom
        ]
        if candidates:
            bottom_index, match = min(
                candidates,
                key=lambda item: abs(float(item[1]["start"]) - float(cue["start"])),
            )
            pairs.append((cue, match))
            paired_top.add(top_index)
            paired_bottom.add(bottom_index)

    return (
        pairs,
        [cue for index, cue in enumerate(top_track) if index not in paired_top],
        [cue for index, cue in enumerate(bottom_track) if index not in paired_bottom],
    )


def _build_synchronized_srt(top_track: Sequence[dict], bottom_track: Sequence[dict]) -> str:
    if not top_track or not bottom_track:
        raise ValueError("Both subtitle tracks must contain cues.")
    top_track = _without_rolling_cues(top_track)
    bottom_track = _without_rolling_cues(bottom_track)
    pairs, unmatched_top, unmatched_bottom = _pair_same_end_cues(
        top_track, bottom_track
    )

    def as_cues(track: Sequence[dict]) -> list[dict]:
        return [
            {
                "start": float(cue["start"]),
                "end": float(cue["start"]) + float(cue["duration"]),
                "text": str(cue["text"]),
            }
            for cue in track
        ]

    top = as_cues(unmatched_top)
    bottom = as_cues(unmatched_bottom)
    all_cues = as_cues((*top_track, *bottom_track))
    if any(cue["end"] <= cue["start"] or not cue["text"].strip() for cue in all_cues):
        raise ValueError("Cannot serialize empty or invalid subtitle cues.")

    boundaries = sorted(
        {cue[key] for cue in (*top, *bottom) for key in ("start", "end")}
    )
    segments = [
        {
            "start": min(float(top_cue["start"]), float(bottom_cue["start"])),
            "end": float(top_cue["start"]) + float(top_cue["duration"]),
            "top": _single_caption_line(str(top_cue["text"])),
            "bottom": _single_caption_line(str(bottom_cue["text"])),
        }
        for top_cue, bottom_cue in pairs
    ]
    timeline_segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        active_top = sorted(
            (cue for cue in top if cue["start"] < end and cue["end"] > start),
            key=lambda cue: cue["start"],
        )
        active_bottom = sorted(
            (cue for cue in bottom if cue["start"] < end and cue["end"] > start),
            key=lambda cue: cue["start"],
        )
        top_text = _single_caption_line("\n".join(cue["text"] for cue in active_top))
        bottom_text = _single_caption_line(
            "\n".join(cue["text"] for cue in active_bottom)
        )
        if not top_text and not bottom_text:
            continue
        if timeline_segments and timeline_segments[-1]["end"] == start and (
            timeline_segments[-1]["top"], timeline_segments[-1]["bottom"]
        ) == (top_text, bottom_text):
            timeline_segments[-1]["end"] = end
        else:
            timeline_segments.append(
                {"start": start, "end": end, "top": top_text, "bottom": bottom_text}
            )
    segments.extend(timeline_segments)
    segments.sort(key=lambda segment: (segment["start"], segment["end"]))
    lines = []
    for index, segment in enumerate(segments, 1):
        text = "\n".join(value for value in (segment["top"], segment["bottom"]) if value)
        lines.extend(
            (
                str(index),
                f"{_format_timestamp(segment['start'])} --> {_format_timestamp(segment['end'])}",
                text,
                "",
            )
        )
    return "\n".join(lines).rstrip("\n") + "\n"


def _fetch_title(video_id: str) -> str:
    url = "https://www.youtube.com/oembed?" + urlencode(
        {"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}
    )
    with urlopen(url, timeout=15) as response:
        return str(json.load(response)["title"])


def _event_date(title: str) -> str:
    for fmt in ("%b. %d, %Y", "%b %d, %Y", "%B %d, %Y"):
        match = re.search(r"([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})", title)
        if match:
            try:
                return datetime.strptime(match.group(1), fmt).date().isoformat()
            except ValueError:
                continue
    return date.today().isoformat()


def _safe_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "youtube-video"


def _transcript_entries(transcript) -> List[dict]:
    return [
        {"start": item.start, "duration": item.duration, "text": item.text}
        for item in transcript.fetch()
    ]


def _parse_srt(content: str) -> List[dict]:
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(
        r"\d+\s*\n(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
        r"(\d{2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )

    def seconds(timestamp: str) -> float:
        hours, minutes, rest = timestamp.replace(",", ".").split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(rest)

    entries = []
    for match in pattern.finditer(normalized):
        start = seconds(match.group(1))
        end = seconds(match.group(2))
        text = match.group(3)
        while text.startswith("\n"):
            text = text[1:]
        while text.endswith("\n"):
            text = text[:-1]
        if not text.strip():
            continue
        entries.append({"start": start, "duration": end - start, "text": text})
    if not entries:
        raise ValueError("yt-dlp returned an empty or unreadable SRT file.")
    return entries


def _run_yt_dlp(arguments: Sequence[str]) -> subprocess.CompletedProcess:
    executable = shutil.which("yt-dlp")
    if not executable:
        raise RuntimeError("yt-dlp is required for organized YouTube subtitle exports.")
    failures = []
    for client in ("default", "ios", "android", "tv_embedded", "mweb"):
        command = [executable, *arguments]
        if client != "default":
            command[1:1] = ["--extractor-args", f"youtube:player_client={client}"]
        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.CalledProcessError as error:
            failures.append((error.stderr or error.stdout or str(error)).strip())
    raise RuntimeError("yt-dlp failed for all player clients:\n" + "\n---\n".join(failures))


def _find_track(candidates: Sequence[str], available: dict) -> str | None:
    codes = {code.lower(): code for code in available}
    return next((codes[code.lower()] for code in candidates if code.lower() in codes), None)


CAPTION_FORMATS = ("vtt", "srt", "ttml", "srv3", "srv2", "srv1", "json3")


def _track_formats(
    manual_tracks: dict, automatic_tracks: dict, language: str
) -> List[str]:
    """List the caption formats YouTube publishes for one language."""
    for tracks in (automatic_tracks, manual_tracks):
        for code, entries in tracks.items():
            if code.lower() != language.lower() or not entries:
                continue
            formats = []
            for entry in entries:
                if isinstance(entry, dict) and entry.get("ext"):
                    formats.append(str(entry["ext"]))
            if formats:
                return formats
    return []


def _download_yt_dlp_track(
    video_url: str,
    video_id: str,
    language: str,
    source_kind: str,
    directory: Path,
    sleep_seconds: int = 56,
    formats: Sequence[str] | None = None,
) -> tuple[Path, List[dict], List[Path]]:
    """Download one caption track and keep every published format.

    The SubRip track is returned for the generated SRT files; the other
    formats are kept next to it so the archive holds the complete set.
    """
    flag = "--write-subs" if source_kind == "manual" else "--write-auto-subs"
    template = str(directory / "%(id)s.%(language)s.%(ext)s")
    requested = [
        extension
        for extension in CAPTION_FORMATS
        if formats is None or extension in set(formats)
    ]
    if not requested:
        requested = ["srt"]
    for extension in requested:
        _run_yt_dlp(
            [
                "--no-warnings",
                "--skip-download",
                "--retries",
                "5",
                "--sleep-subtitles",
                str(sleep_seconds),
                flag,
                "--sub-langs",
                language,
                "--sub-format",
                extension,
                "-o",
                template,
                video_url,
            ]
        )
    candidates = [
        path
        for path in directory.glob(f"{video_id}.*")
        if path.is_file() and path.name.lower().endswith(f".{language.lower()}.srt")
    ]
    if not candidates:
        raise RuntimeError(f"yt-dlp completed but did not create a {language} subtitle file.")
    source_path = candidates[0]
    downloaded = sorted(
        path
        for path in directory.glob(f"{video_id}.*")
        if path.is_file()
        and path.name.lower().endswith(
            tuple(f".{language.lower()}.{extension}" for extension in CAPTION_FORMATS)
        )
    )
    return source_path, _parse_srt(source_path.read_text(encoding="utf-8-sig")), downloaded


def save_youtube_archive(
    video_url: str,
    output_dir: str | Path = "output",
    *,
    event: str | None = None,
    event_date: str | None = None,
    english_language: str = "en-orig",
    chinese_language: str = "zh-Hans",
    sleep_seconds: int = 56,
) -> Path:
    video_id = extract_video_id(video_url)
    metadata = json.loads(
        _run_yt_dlp(["--no-warnings", "--skip-download", "--dump-single-json", video_url]).stdout
    )
    manual_tracks = metadata.get("subtitles", {})
    automatic_tracks = metadata.get("automatic_captions", {})
    english_options = [english_language] + [
        code for code in ("en-orig", "en") if code != english_language
    ]
    chinese_options = [chinese_language] + [
        code
        for code in ("zh-Hans", "zh-CN", "zh-Hant", "zh-TW", "zh")
        if code != chinese_language
    ]
    english_code = _find_track(english_options, manual_tracks)
    english_kind = "manual"
    if english_code is None:
        english_code = _find_track(english_options, automatic_tracks)
        english_kind = "auto-generated"
    if english_code is None:
        raise ValueError("No English transcript is available for this video.")

    chinese_code = _find_track(chinese_options, manual_tracks)
    chinese_kind = "manual"
    if chinese_code is None:
        chinese_code = _find_track(chinese_options, automatic_tracks)
        chinese_kind = (
            "auto-translated" if chinese_code and english_code.startswith("en") else "auto-generated"
        )
    if chinese_code is None:
        raise SubtitleTranslationError("YouTube does not list a Chinese subtitle track for this video.")

    title = event or str(metadata.get("title") or _fetch_title(video_id))
    upload_date = str(metadata.get("upload_date") or "")
    default_date = (
        datetime.strptime(upload_date, "%Y%m%d").date().isoformat()
        if re.fullmatch(r"\d{8}", upload_date)
        else _event_date(title)
    )
    folder_date = event_date or (_event_date(title) if re.search(r"[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}", title) else default_date)
    try:
        folder_date = date.fromisoformat(folder_date).isoformat()
    except ValueError as error:
        raise ValueError("Event date must use YYYY-MM-DD format.") from error
    folder = Path(output_dir) / f"{folder_date}_{_safe_slug(title)}_{video_id}"
    folder.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="youtube-captions-") as temp:
        temp_dir = Path(temp)
        english_path, english, english_files = _download_yt_dlp_track(
            video_url,
            video_id,
            english_code,
            english_kind,
            temp_dir,
            sleep_seconds=0,
            formats=_track_formats(manual_tracks, automatic_tracks, english_code),
        )
        chinese_path, chinese, chinese_files = _download_yt_dlp_track(
            video_url,
            video_id,
            chinese_code,
            chinese_kind,
            temp_dir,
            sleep_seconds=sleep_seconds,
            formats=_track_formats(manual_tracks, automatic_tracks, chinese_code),
        )
        english_source = english_path.read_bytes()
        chinese_source = chinese_path.read_bytes()
        english_formats = [(path, path.read_bytes()) for path in english_files]
        chinese_formats = [(path, path.read_bytes()) for path in chinese_files]

    (folder / f"{english_kind}_English.srt").write_bytes(english_source)
    (folder / f"{chinese_kind}_Chinese-{chinese_code}.srt").write_bytes(chinese_source)
    for path, payload in english_formats:
        suffix = path.suffix.lower().lstrip(".")
        if suffix == "srt":
            continue
        (folder / f"{english_kind}_English.{suffix}").write_bytes(payload)
    for path, payload in chinese_formats:
        suffix = path.suffix.lower().lstrip(".")
        if suffix == "srt":
            continue
        (folder / f"{chinese_kind}_Chinese-{chinese_code}.{suffix}").write_bytes(payload)
    bilingual_kind = (
        "auto-translated"
        if chinese_kind == "auto-translated"
        else english_kind
        if english_kind == chinese_kind
        else "mixed"
    )
    (folder / f"{bilingual_kind}_Bilingual.srt").write_text(
        _build_synchronized_srt(chinese, english),
        encoding="utf-8-sig",
    )
    (folder / "INFO.txt").write_text(
        f"Video: {title}\n"
        f"Video ID: {video_id}\n"
        f"Event date: {folder_date}\n"
        f"Source: https://www.youtube.com/watch?v={video_id}\n"
        f"English subtitle type: {english_kind} (not independently proofread)\n"
        f"Chinese subtitle type: {chinese_kind} ({chinese_code})\n",
        encoding="utf-8",
    )
    return folder


def srt_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download YouTube English and Chinese subtitle tracks and create a bilingual SRT."
    )
    parser.add_argument("--url", required=True, help="YouTube URL or video ID.")
    parser.add_argument("--output-dir", default="output", help="Output folder.")
    parser.add_argument("--event", help="Optional event title for the output folder.")
    parser.add_argument("--date", help="Optional event date in YYYY-MM-DD format.")
    parser.add_argument("--english", default="en-orig", help="English caption track (default: en-orig).")
    parser.add_argument("--chinese", default="zh-Hans", help="Chinese caption track (default: zh-Hans).")
    parser.add_argument(
        "--sleep-subtitles",
        type=int,
        default=56,
        help="Seconds yt-dlp waits before downloading the translated track (default: 56).",
    )
    args = parser.parse_args(argv)
    if args.sleep_subtitles < 0:
        parser.error("--sleep-subtitles must not be negative")
    folder = save_youtube_archive(
        args.url,
        args.output_dir,
        event=args.event,
        event_date=args.date,
        english_language=args.english,
        chinese_language=args.chinese,
        sleep_seconds=args.sleep_subtitles,
    )
    print(folder)
    return 0


def translate_text(text: str, *, target_lang: str = "zh-CN", translator=None) -> str:
    if not text:
        return ""
    for attempt in range(3):
        try:
            if translator is None:
                translator = GoogleTranslator(source="en", target=target_lang)
            return str(translator.translate(text))
        except TooManyRequests as error:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            raise SubtitleTranslationError(
                "Translation service rate-limited the request after 3 attempts."
            ) from error
        except Exception as error:
            raise SubtitleTranslationError(
                f"Translation service failed: {error}"
            ) from error
    raise SubtitleTranslationError("Translation failed after repeated attempts.")


def build_bilingual_srt(entries: Sequence[dict], *, target_lang: str = "zh-CN", translator=None) -> str:
    lines: List[str] = []
    for index, entry in enumerate(entries, start=1):
        start = float(entry.get("start", 0.0))
        duration = float(entry.get("duration", 0.0))
        en_text = str(entry.get("text", "")).strip()
        zh_text = translate_text(en_text, target_lang=target_lang, translator=translator) if en_text else ""
        lines.append(str(index))
        lines.append(f"{_format_timestamp(start)} --> {_format_timestamp(start + duration)}")
        lines.append(zh_text)
        lines.append(en_text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def save_bilingual_srt(
    video_url: str,
    output_path: str | Path,
    *,
    target_lang: str = "zh-CN",
    source: str = "yt-dlp",
) -> str:
    entries = fetch_transcript(video_url, source=source)
    srt = build_bilingual_srt(entries, target_lang=target_lang)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(srt, encoding="utf-8")
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch a YouTube transcript and save a bilingual Chinese/English SRT file.")
    parser.add_argument("--url", required=True, help="YouTube watch URL or video ID.")
    parser.add_argument("--output", default="bilingual.srt", help="Output SRT path.")
    parser.add_argument("--lang", default="zh-CN", help="Target translation language for the Chinese line, e.g. zh-CN.")
    parser.add_argument(
        "--source",
        choices=("yt-dlp", "transcript-api"),
        default="yt-dlp",
        help=(
            "Transcript source: YouTube auto-generated subtitle track via yt-dlp "
            "(default), or youtube-transcript-api."
        ),
    )
    args = parser.parse_args(argv)
    try:
        path = save_bilingual_srt(
            args.url, args.output, target_lang=args.lang, source=args.source
        )
    except (SubtitleTranslationError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(path)
    return 0


def archive_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Save available YouTube subtitles in a date/event/video-ID folder."
    )
    parser.add_argument("--url", required=True, help="YouTube watch URL or video ID.")
    parser.add_argument("--output-dir", default="output", help="Subtitle archive root directory.")
    parser.add_argument("--event", help="Override the video title used as the event folder name.")
    parser.add_argument("--date", help="Override folder date (YYYY-MM-DD).")
    args = parser.parse_args(argv)
    folder = save_youtube_archive(
        args.url, args.output_dir, event=args.event, event_date=args.date
    )
    print(folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
