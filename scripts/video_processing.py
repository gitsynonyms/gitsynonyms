#!/usr/bin/env python3
"""Module 2 - video processing department: burn selected subtitles into a video.

This module starts *after* the selection is made. It never downloads anything:
it takes one video file and the subtitle file(s) that were already chosen
upstream, validates that they belong together, burns the subtitle track into
the picture with one FFmpeg libass pass, optionally renders a small preview and
a poster frame, and writes ``BURN.json`` as the hand-off to the publishing
module.

Input is either a selection manifest (``--selection``) or explicit paths
(``--video`` plus ``--subtitle``). A manifest looks like this; relative paths
are resolved against the manifest's directory first and the current directory
second::

    {
      "video_id": "TLH3gXrPCTU",
      "title": "President Trump Participates in a Roundtable, Oct. 21, 2026",
      "event_date": "2026-10-21",
      "video": "../work/TLH3gXrPCTU.source.mp4",
      "subtitles": {
        "ass": "output/2026-10-21_.../bilingual_Chinese-English.ass",
        "srt": "output/2026-10-21_.../bilingual_Chinese-English.srt",
        "vtt": "output/2026-10-21_.../bilingual_Chinese-English.vtt"
      },
      "output_dir": "output/2026-10-21_...",
      "preview": true,
      "poster": true
    }

Among the listed subtitles the styled ASS is preferred, because it carries the
layout, colors, and per-word timing; SRT/VTT are accepted as fallbacks and are
rendered by libass with its default style.

Usage::

    python3 scripts/video_processing.py --selection selection.json
    python3 scripts/video_processing.py \\
        --video source.mp4 --subtitle bilingual.ass \\
        --video-id TLH3gXrPCTU --event-date 2026-10-21 \\
        --output-dir output/2026-10-21_TLH3gXrPCTU --preview

Exit codes: 0 on burned/reused/dry-run success, 1 on any validation or FFmpeg
failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Reuse the repository's caption parsers so a subtitle is read exactly the same
# way here as it was written by the downloader.
import download_youtube_bilingual_srt as captions  # noqa: E402

MODULE_NAME = "module2-video-processing"
MODULE_VERSION = "1.0"

# Preference order for a selection that offers several subtitle files.
SUBTITLE_PREFERENCE = ("ass", "srt", "vtt", "ttml", "srv3", "srv2", "srv1", "json3")
DEFAULT_CRF = 20
DEFAULT_PRESET = "veryfast"
DEFAULT_ENCODER = "libx264"
PREVIEW_HEIGHT = 540
PREVIEW_CRF = 28
POSTER_HEIGHT = 540
POSTER_AT_FRACTION = 0.1
# A caption may end a fraction of a second after the last video frame; anything
# beyond that means the subtitle belongs to a different cut of the video.
OVERRUN_TOLERANCE = 0.5
DURATION_TOLERANCE = 2.0
SHA_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Selection:
    """One video plus the subtitle track chosen for it."""

    video: Path
    subtitle: Path
    output: Path
    video_id: str
    title: str = ""
    event_date: str = ""
    output_dir: Path | None = None
    preview: Path | None = None
    poster: Path | None = None
    manifest: Path | None = None


@dataclass(frozen=True)
class BurnOptions:
    encoder: str = DEFAULT_ENCODER
    crf: int = DEFAULT_CRF
    preset: str = DEFAULT_PRESET
    force: bool = False
    dry_run: bool = False
    strict: bool = False
    strict_fonts: bool = False
    check_fonts: bool = True
    report: Path | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def tool_path(name: str) -> str | None:
    """Locate an FFmpeg tool, honouring an explicit environment override."""
    override = os.environ.get(name.upper())
    if override:
        return override
    return shutil.which(name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(SHA_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_video_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_")
    if not cleaned or len(cleaned) > 64:
        raise ValueError(f"Invalid video id: {value!r}")
    return cleaned


def _resolve_input(value: str, bases: Sequence[Path]) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    for base in bases:
        candidate = (base / raw).resolve()
        if candidate.exists():
            return candidate
    return (bases[0] / raw).resolve()


def _resolve_output(value: str, bases: Sequence[Path]) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    return (bases[0] / raw).resolve()


def _subtitle_paths(value: object, bases: Sequence[Path]) -> list[Path]:
    """Return every subtitle path a manifest entry offers, best format first."""
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, list):
        raw = [str(item) for item in value if item]
    elif isinstance(value, dict):
        ordered = [key for key in SUBTITLE_PREFERENCE if value.get(key)]
        ordered += [key for key in value if key not in SUBTITLE_PREFERENCE and value.get(key)]
        raw = [str(value[key]) for key in ordered]
    else:
        raise ValueError("Selection subtitle entry must be a path, a list, or a mapping.")
    if not raw:
        raise ValueError("Selection does not name any subtitle file.")
    paths = [_resolve_input(item, bases) for item in raw]
    rank = {extension: index for index, extension in enumerate(SUBTITLE_PREFERENCE)}
    paths.sort(key=lambda path: rank.get(path.suffix.lower().lstrip("."), len(rank)))
    return paths


def _pick_subtitle(paths: Sequence[Path]) -> Path:
    for path in paths:
        if path.is_file():
            return path
    listed = ", ".join(str(path) for path in paths)
    raise ValueError(f"None of the selected subtitle files exists: {listed}")


def _default_output(output_dir: Path, video_id: str) -> Path:
    return output_dir / f"Burned_Bilingual_{video_id}.mp4"


def _optional_path(value: object, default: Path, bases: Sequence[Path], label: str) -> Path | None:
    if value in (None, False):
        return None
    if value is True:
        return default
    if isinstance(value, str) and value.strip():
        return _resolve_output(value, bases)
    raise ValueError(f"Selection {label} entry must be true, false, or a path.")


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"Selection manifest not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Selection manifest is not valid JSON: {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"Selection manifest must contain a JSON object: {path}")
    return data


def selection_from_manifest(manifest_path: Path) -> Selection:
    manifest_path = manifest_path.resolve()
    data = load_manifest(manifest_path)
    bases = [manifest_path.parent, Path.cwd()]

    video_value = data.get("video") or data.get("source_video") or data.get("video_file")
    if not isinstance(video_value, str) or not video_value.strip():
        raise ValueError("Selection manifest must name the selected video file under 'video'.")
    video = _resolve_input(video_value, bases)
    if not video.is_file():
        raise ValueError(f"Selected video file not found: {video}")

    video_id_value = data.get("video_id")
    if isinstance(video_id_value, str) and video_id_value.strip():
        video_id = _sanitize_video_id(video_id_value)
    else:
        stem = re.sub(r"\.(source|video|original)$", "", video.stem)
        video_id = _sanitize_video_id(stem)

    subtitle_value = data.get("subtitles", data.get("subtitle"))
    if subtitle_value is None:
        raise ValueError("Selection manifest must name the subtitle file(s) under 'subtitles'.")
    subtitle = _pick_subtitle(_subtitle_paths(subtitle_value, bases))

    output_dir_value = data.get("output_dir")
    output_dir = _resolve_output(str(output_dir_value), bases) if output_dir_value else video.parent
    output_value = data.get("output")
    output = (
        _resolve_output(str(output_value), bases)
        if output_value
        else _default_output(output_dir, video_id)
    )
    preview = _optional_path(
        data.get("preview"),
        output.with_name(f"{output.stem}-540p.mp4"),
        bases,
        "preview",
    )
    poster = _optional_path(
        data.get("poster"),
        output.with_name(f"{output.stem}-poster.jpg"),
        bases,
        "poster",
    )
    return Selection(
        video=video,
        subtitle=subtitle,
        output=output,
        video_id=video_id,
        title=str(data.get("title") or ""),
        event_date=str(data.get("event_date") or data.get("date") or ""),
        output_dir=output_dir,
        preview=preview,
        poster=poster,
        manifest=manifest_path,
    )


def selection_from_args(args: argparse.Namespace) -> Selection:
    video = Path(args.video).expanduser().resolve()
    subtitle = Path(args.subtitle).expanduser().resolve()
    if not video.is_file():
        raise ValueError(f"Selected video file not found: {video}")
    if not subtitle.is_file():
        raise ValueError(f"Selected subtitle file not found: {subtitle}")
    stem = re.sub(r"\.(source|video|original)$", "", video.stem)
    video_id = _sanitize_video_id(args.video_id or stem)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else video.parent
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else _default_output(output_dir, video_id)
    )
    preview = None
    if args.preview or args.preview_path:
        preview = (
            Path(args.preview_path).expanduser().resolve()
            if args.preview_path
            else output.with_name(f"{output.stem}-540p.mp4")
        )
    poster = None
    if args.poster or args.poster_path:
        poster = (
            Path(args.poster_path).expanduser().resolve()
            if args.poster_path
            else output.with_name(f"{output.stem}-poster.jpg")
        )
    return Selection(
        video=video,
        subtitle=subtitle,
        output=output,
        video_id=video_id,
        title=args.title or "",
        event_date=args.event_date or "",
        output_dir=output_dir,
        preview=preview,
        poster=poster,
        manifest=None,
    )


def probe_media(path: Path, ffprobe: str | None = None) -> dict:
    """Read container and stream metadata with ffprobe."""
    executable = ffprobe or tool_path("ffprobe")
    if not executable:
        raise RuntimeError("ffprobe is required to validate the selected video but was not found.")
    command = [
        executable,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as error:
        raise RuntimeError("ffprobe is required to validate the selected video but was not found.") from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or str(error)).strip()
        raise RuntimeError(f"ffprobe could not read {path.name}: {details}") from error
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path.name}.") from error


def _stream_duration(stream: dict) -> float | None:
    for key in ("duration", "duration_ts"):
        try:
            value = float(stream.get(key))
        except (TypeError, ValueError):
            continue
        if key == "duration_ts":
            time_base = stream.get("time_base") or ""
            try:
                numerator, denominator = (int(part) for part in time_base.split("/"))
            except (TypeError, ValueError):
                continue
            if denominator:
                value = value * numerator / denominator
        if value > 0:
            return value
    return None


def summarize_probe(probe: dict) -> dict:
    streams = probe.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise RuntimeError("The selected file has no video stream.")
    duration = None
    try:
        duration = float((probe.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        pass
    if not duration or duration <= 0:
        duration = _stream_duration(video_stream)
    if not duration or duration <= 0:
        raise RuntimeError("The selected video has no readable duration.")
    return {
        "duration_seconds": round(duration, 3),
        "size_bytes": int((probe.get("format") or {}).get("size") or 0),
        "container": (probe.get("format") or {}).get("format_name") or "",
        "video": {
            "codec": video_stream.get("codec_name") or "",
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
            "frame_rate": video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "",
            "pixel_format": video_stream.get("pix_fmt") or "",
        },
        "audio": (
            {
                "codec": audio_stream.get("codec_name") or "",
                "channels": int(audio_stream.get("channels") or 0),
                "sample_rate": int(audio_stream.get("sample_rate") or 0),
            }
            if audio_stream
            else None
        ),
    }


def ass_fonts(path: Path) -> list[str]:
    """Return the font families referenced by an ASS/SSA style table."""
    fonts: list[str] = []
    fields: list[str] | None = None
    in_styles = False
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_styles = stripped.lower() in {"[v4+ styles]", "[v4 styles]"}
            fields = None
            continue
        if not in_styles:
            continue
        if stripped.lower().startswith("format:"):
            fields = [item.strip().lower() for item in stripped.split(":", 1)[1].split(",")]
        elif stripped.lower().startswith("style:"):
            values = stripped.split(":", 1)[1].split(",")
            index = fields.index("fontname") if fields and "fontname" in fields else 1
            if len(values) > index:
                font = values[index].strip()
                if font and font not in fonts:
                    fonts.append(font)
    return fonts


def check_fonts(fonts: Sequence[str]) -> dict:
    """Ask fontconfig how each requested ASS family will actually be rendered."""
    if not fonts:
        return {"checked": False, "reason": "the subtitle does not name any font", "fonts": []}
    executable = shutil.which("fc-match")
    if not executable:
        return {"checked": False, "reason": "fc-match is not installed", "fonts": []}
    matches = []
    for font in fonts:
        try:
            result = subprocess.run(
                [executable, "-f", "%{family}", font],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return {"checked": False, "reason": "fc-match could not run", "fonts": []}
        resolved = (result.stdout or "").split(",")[0].strip()
        matches.append(
            {
                "requested": font,
                "resolved": resolved,
                "exact": bool(resolved) and resolved.lower() == font.lower(),
            }
        )
    return {"checked": True, "fonts": matches}


ASS_TIMESTAMP = re.compile(r"^(\d+):(\d{2}):(\d{2})[.](\d{1,2})$")


def ass_time_to_seconds(value: str) -> float:
    match = ASS_TIMESTAMP.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid ASS timestamp: {value!r}")
    hours, minutes, seconds, fraction = match.groups()
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction) / (10 ** len(fraction))
    )


def parse_ass_cues(path: Path) -> list[dict]:
    """Parse ASS/SSA dialogue lines, including the drawing events of the style."""
    cues: list[dict] = []
    fields: list[str] | None = None
    in_events = False
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.lower() == "[events]"
            fields = None
            continue
        if not in_events:
            continue
        lowered = stripped.lower()
        if lowered.startswith("format:"):
            fields = [item.strip().lower() for item in stripped.split(":", 1)[1].split(",")]
        elif lowered.startswith("dialogue:"):
            if not fields or "start" not in fields or "end" not in fields:
                raise ValueError(f"ASS file has a Dialogue before its Format line: {path.name}")
            values = stripped.split(":", 1)[1].lstrip().split(",", len(fields) - 1)
            if len(values) < len(fields):
                raise ValueError(f"ASS file has a malformed Dialogue line: {path.name}")
            record = dict(zip(fields, values))
            text = record.get("text", "")
            cues.append(
                {
                    "start": ass_time_to_seconds(record["start"]),
                    "end": ass_time_to_seconds(record["end"]),
                    "text": text,
                    # Styles such as the caption box are vector drawings, not
                    # spoken text; they must not drive the timing validation.
                    "drawing": bool(re.search(r"\\p[1-9]", text)),
                }
            )
    return cues


def inspect_subtitles(path: Path, video_duration: float) -> dict:
    extension = path.suffix.lower().lstrip(".")
    drawing_cues = 0
    if extension in {"ass", "ssa"}:
        all_cues = parse_ass_cues(path)
        cues = [cue for cue in all_cues if not cue["drawing"]]
        drawing_cues = len(all_cues) - len(cues)
    else:
        cues = captions.parse_caption_file(path)
    if not cues:
        raise ValueError(f"Subtitle file contains no cues: {path.name}")
    first_start = min(float(cue["start"]) for cue in cues)
    last_end = max(float(cue["end"]) for cue in cues)
    covered = sum(
        max(0.0, min(float(cue["end"]), video_duration) - float(cue["start"])) for cue in cues
    )
    info = {
        "path": str(path),
        "format": extension,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "cues": len(cues),
        "drawing_cues": drawing_cues,
        "first_cue_seconds": round(first_start, 3),
        "last_cue_seconds": round(last_end, 3),
        "caption_coverage_seconds": round(min(covered, video_duration), 3),
        "caption_coverage_percent": round(min(covered, video_duration) * 100 / video_duration, 1),
        "overrun_seconds": round(max(0.0, last_end - video_duration), 3),
        "fonts": [],
    }
    if extension in {"ass", "ssa"}:
        info["fonts"] = ass_fonts(path)
    return info


def escape_filter_path(path: Path) -> str:
    """Escape a path inside FFmpeg's subtitles= filter argument."""
    value = str(path.resolve()).replace("\\", "/")
    return (
        value.replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


MP4_AUDIO_CODECS = {"aac", "mp3", "mp2", "ac3", "eac3", "alac"}


def choose_audio_mode(output: Path, audio: dict | None) -> str:
    """Copy audio into MP4 when the source codec is allowed, otherwise encode AAC."""
    if output.suffix.lower() not in {".mp4", ".m4v", ".mov"}:
        return "copy"
    codec = (audio or {}).get("codec") or ""
    if not codec or codec in MP4_AUDIO_CODECS:
        return "copy"
    return "aac"


def build_burn_command(
    ffmpeg: str,
    video: Path,
    subtitle: Path,
    output: Path,
    *,
    encoder: str = DEFAULT_ENCODER,
    crf: int = DEFAULT_CRF,
    preset: str = DEFAULT_PRESET,
    audio: str = "copy",
) -> list[str]:
    if encoder not in {"libx264", "h264_qsv"}:
        raise ValueError("Video encoder must be 'libx264' or 'h264_qsv'.")
    if not 1 <= crf <= 51:
        raise ValueError("Quality value must be between 1 and 51.")
    if not preset.strip():
        raise ValueError("Encoder preset must not be empty.")
    if audio not in {"copy", "aac"}:
        raise ValueError("Audio mode must be 'copy' or 'aac'.")
    command = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(video),
        "-vf",
        f"subtitles=filename='{escape_filter_path(subtitle)}'",
        "-c:v",
        encoder,
    ]
    if encoder == "h264_qsv":
        command += ["-global_quality", str(crf)]
    else:
        command += ["-preset", preset, "-crf", str(crf)]
    command += ["-pix_fmt", "yuv420p", "-c:a", audio]
    if audio == "aac":
        command += ["-b:a", "192k"]
    if output.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        command += ["-movflags", "+faststart"]
    command.append(str(output))
    return command


def build_preview_command(
    ffmpeg: str,
    burned: Path,
    preview: Path,
    *,
    source_height: int,
    height: int = PREVIEW_HEIGHT,
    crf: int = PREVIEW_CRF,
) -> list[str]:
    target_height = max(2, min(height, source_height or height))
    target_height -= target_height % 2
    return [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(burned),
        "-vf",
        f"scale=-2:{target_height}:flags=lanczos",
        "-c:v",
        "libx264",
        "-preset",
        DEFAULT_PRESET,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        str(preview),
    ]


def build_poster_command(
    ffmpeg: str,
    burned: Path,
    poster: Path,
    *,
    at_seconds: float,
) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-ss",
        f"{max(0.0, at_seconds):.3f}",
        "-i",
        str(burned),
        "-frames:v",
        "1",
        "-vf",
        f"scale=-2:{POSTER_HEIGHT}:flags=lanczos",
        "-q:v",
        "3",
        str(poster),
    ]


def run_command(command: Sequence[str], label: str) -> str:
    """Run an FFmpeg command and return its stderr tail."""
    try:
        result = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"{label} requires {command[0]}, which was not found.") from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or str(error)).strip().splitlines()
        tail = "\n".join(details[-12:])
        raise RuntimeError(f"{label} failed with exit code {error.returncode}: {tail}") from error
    return (result.stderr or "").strip()


def ffmpeg_version(ffmpeg: str) -> str:
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return (result.stdout or "").splitlines()[0].strip() if result.stdout else "unknown"


def summarize_render_log(stderr: str) -> tuple[int, list[str]]:
    """Condense FFmpeg/libass stderr into a bounded list of useful warnings.

    libass prints one ``Glyph 0x.... not found`` line per missing character, so
    a missing CJK font can produce thousands of lines. Those are counted and
    reported as one warning instead of being copied into BURN.json.
    """
    missing_glyphs = len(set(re.findall(r"Glyph 0x([0-9A-Fa-f]+) not found", stderr)))
    warnings: list[str] = []
    for line in stderr.splitlines():
        text = line.strip()
        if not text or re.search(r"Glyph 0x|failed to find any fallback", text):
            continue
        if not re.search(r"fontconfig|fontselect|fallback|deprecated", text, re.IGNORECASE):
            continue
        text = re.sub(r"^\[[^\]]+\]\s*", "", text)[:300]
        if text not in warnings:
            warnings.append(text)
    if missing_glyphs:
        warnings.append(
            f"Rendering could not find glyphs for {missing_glyphs} characters; the fonts "
            "named by the ASS file are missing on this machine, so that text is not drawn."
        )
    return missing_glyphs, warnings[:20]


def _file_record(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "sha256": sha256_file(path),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _report_path(selection: Selection, options: BurnOptions) -> Path:
    if options.report:
        return options.report.resolve()
    return (selection.output_dir or selection.output.parent) / "BURN.json"


def validate_output_paths(selection: Selection) -> None:
    """Refuse to write over the selected inputs or over another output."""
    outputs = {
        "output": selection.output,
        "preview": selection.preview,
        "poster": selection.poster,
    }
    inputs = {"video": selection.video, "subtitle": selection.subtitle}
    seen: dict[Path, str] = {}
    for label, path in outputs.items():
        if path is None:
            continue
        resolved = path.resolve()
        if resolved in seen:
            raise ValueError(
                f"The {label} output and the {seen[resolved]} output point at the same file: {path}"
            )
        seen[resolved] = label
        for input_label, input_path in inputs.items():
            if resolved == input_path.resolve():
                raise ValueError(
                    f"The {label} output would overwrite the selected {input_label}: {path}"
                )


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_fresh(output: Path, inputs: Sequence[Path]) -> bool:
    if not output.is_file() or output.stat().st_size == 0:
        return False
    output_time = output.stat().st_mtime
    return all(output_time >= path.stat().st_mtime for path in inputs)


def process(selection: Selection, options: BurnOptions) -> dict:
    """Validate the selection, burn it, and write the BURN.json hand-off."""
    report_path = _report_path(selection, options)
    report: dict = {
        "module": MODULE_NAME,
        "version": MODULE_VERSION,
        "status": "starting",
        "created_at": utc_now(),
        "selection": {
            "video_id": selection.video_id,
            "title": selection.title,
            "event_date": selection.event_date,
            "manifest": str(selection.manifest) if selection.manifest else None,
        },
        "inputs": {},
        "video": {},
        "subtitles": {},
        "fonts": {},
        "render": {},
        "burn": {},
        "outputs": {},
        "warnings": [],
    }
    try:
        validate_output_paths(selection)
        ffmpeg = tool_path("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required for burn-in but was not found on PATH.")
        report["burn"]["tool"] = ffmpeg_version(ffmpeg)

        video_info = summarize_probe(probe_media(selection.video))
        subtitle_info = inspect_subtitles(selection.subtitle, video_info["duration_seconds"])
        report["video"] = video_info
        report["subtitles"] = subtitle_info
        report["inputs"] = {
            "video": _file_record(selection.video),
            "subtitle": _file_record(selection.subtitle),
        }
        if subtitle_info["overrun_seconds"] > OVERRUN_TOLERANCE:
            message = (
                f"The subtitle runs {subtitle_info['overrun_seconds']:.3f} s past the end of "
                f"the {video_info['duration_seconds']:.3f} s video."
            )
            if options.strict:
                raise ValueError(message)
            report["warnings"].append(message)

        if options.check_fonts:
            font_report = check_fonts(subtitle_info["fonts"])
            report["fonts"] = font_report
            for match in font_report.get("fonts", []):
                if match.get("resolved") and not match["exact"]:
                    report["warnings"].append(
                        f"Font {match['requested']!r} resolves to {match['resolved']!r}; "
                        "the render uses the substituted family."
                    )

        print(f"Module 2 - video processing: {selection.video_id}")
        print(
            f"  video    : {selection.video} "
            f"({video_info['video']['width']}x{video_info['video']['height']}, "
            f"{video_info['duration_seconds']:.1f} s)"
        )
        print(
            f"  subtitle : {selection.subtitle.name} "
            f"({subtitle_info['format']}, {subtitle_info['cues']} cues, "
            f"last cue {subtitle_info['last_cue_seconds']:.2f} s)"
        )
        print(f"  output   : {selection.output}")

        reused = (
            _is_fresh(selection.output, [selection.video, selection.subtitle])
            and not options.force
        )
        if not reused:
            audio_mode = choose_audio_mode(selection.output, video_info["audio"])
            temp_output = selection.output.with_name(
                f".{selection.output.stem}.part{selection.output.suffix}"
            )
            command = build_burn_command(
                ffmpeg,
                selection.video,
                selection.subtitle,
                selection.output if options.dry_run else temp_output,
                encoder=options.encoder,
                crf=options.crf,
                preset=options.preset,
                audio=audio_mode,
            )
            report["burn"].update(
                {
                    "encoder": options.encoder,
                    "quality": options.crf,
                    "preset": options.preset if options.encoder == "libx264" else None,
                    "audio": audio_mode,
                    "command": command,
                    "output": str(selection.output),
                }
            )
            if options.dry_run:
                report["status"] = "dry-run"
                print("  dry run  : " + " ".join(command))
                report["outputs"]["video"] = {"path": str(selection.output), "rendered": False}
            else:
                selection.output.parent.mkdir(parents=True, exist_ok=True)
                audio_note = "audio stream copied" if audio_mode == "copy" else "audio re-encoded to AAC"
                print(f"  burning  : single libass pass, {audio_note}...")
                try:
                    stderr = run_command(command, "FFmpeg burn-in")
                    missing_glyphs, render_warnings = summarize_render_log(stderr)
                    report["render"] = {"missing_glyphs": missing_glyphs}
                    report["warnings"].extend(render_warnings)
                    if options.strict_fonts and missing_glyphs:
                        raise RuntimeError(
                            f"The render is missing glyphs for {missing_glyphs} characters; "
                            "install the ASS fonts before publishing this burn-in."
                        )
                    output_probe = summarize_probe(probe_media(temp_output))
                    duration = output_probe["duration_seconds"]
                    if abs(duration - video_info["duration_seconds"]) > max(
                        DURATION_TOLERANCE, video_info["duration_seconds"] * 0.01
                    ):
                        raise RuntimeError(
                            "The burned video duration "
                            f"({duration:.3f} s) does not match the source "
                            f"({video_info['duration_seconds']:.3f} s)."
                        )
                    os.replace(temp_output, selection.output)
                except Exception:
                    temp_output.unlink(missing_ok=True)
                    raise
                report["outputs"]["video"] = {
                    **_file_record(selection.output),
                    "duration_seconds": duration,
                    "width": output_probe["video"]["width"],
                    "height": output_probe["video"]["height"],
                    "video_codec": output_probe["video"]["codec"],
                    "audio_codec": (output_probe["audio"] or {}).get("codec"),
                    "rendered": True,
                }
                report["status"] = "burned"
                print(f"  OK       : {selection.output.name}")
        else:
            report["burn"]["command"] = None
            report["outputs"]["video"] = {**_file_record(selection.output), "rendered": False}
            report["status"] = "reused"
            print("  skipped  : output is newer than both inputs (use --force to re-render)")

        if selection.preview is not None:
            preview_command = None
            if _is_fresh(selection.preview, [selection.output]) and not options.force:
                report["outputs"]["preview"] = {
                    **_file_record(selection.preview),
                    "rendered": False,
                }
            elif options.dry_run:
                preview_command = build_preview_command(
                    ffmpeg,
                    selection.output,
                    selection.preview,
                    source_height=video_info["video"]["height"],
                )
                report["outputs"]["preview"] = {
                    "path": str(selection.preview),
                    "command": preview_command,
                    "rendered": False,
                }
            else:
                preview_command = build_preview_command(
                    ffmpeg,
                    selection.output,
                    selection.preview,
                    source_height=video_info["video"]["height"],
                )
                selection.preview.parent.mkdir(parents=True, exist_ok=True)
                run_command(preview_command, "FFmpeg preview render")
                report["outputs"]["preview"] = {
                    **_file_record(selection.preview),
                    "rendered": True,
                }
                print(f"  preview  : {selection.preview.name}")

        if selection.poster is not None:
            poster_command = None
            if _is_fresh(selection.poster, [selection.output]) and not options.force:
                report["outputs"]["poster"] = {**_file_record(selection.poster), "rendered": False}
            elif options.dry_run:
                poster_command = build_poster_command(
                    ffmpeg,
                    selection.output,
                    selection.poster,
                    at_seconds=video_info["duration_seconds"] * POSTER_AT_FRACTION,
                )
                report["outputs"]["poster"] = {
                    "path": str(selection.poster),
                    "command": poster_command,
                    "rendered": False,
                }
            else:
                poster_command = build_poster_command(
                    ffmpeg,
                    selection.output,
                    selection.poster,
                    at_seconds=video_info["duration_seconds"] * POSTER_AT_FRACTION,
                )
                selection.poster.parent.mkdir(parents=True, exist_ok=True)
                run_command(poster_command, "FFmpeg poster render")
                report["outputs"]["poster"] = {
                    **_file_record(selection.poster),
                    "rendered": True,
                }
                print(f"  poster   : {selection.poster.name}")
    except Exception as error:  # noqa: BLE001 - every failure must reach the report
        report["status"] = "failed"
        report["error"] = str(error)
        report["finished_at"] = utc_now()
        try:
            _write_report(report_path, report)
        except OSError:
            pass
        raise
    report["finished_at"] = utc_now()
    _write_report(report_path, report)
    print(f"  report   : {report_path}")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Module 2: burn the already-selected subtitle track into the selected video."
    )
    parser.add_argument("--selection", type=Path, help="Selection manifest produced upstream.")
    parser.add_argument("--video", help="Selected video file (when no manifest is used).")
    parser.add_argument("--subtitle", help="Selected subtitle file (when no manifest is used).")
    parser.add_argument("--video-id", help="Video id used in output names and the report.")
    parser.add_argument("--title", help="Event title recorded in the report.")
    parser.add_argument("--event-date", help="Event date in YYYY-MM-DD format.")
    parser.add_argument("--output-dir", help="Directory for the burn-in and BURN.json.")
    parser.add_argument("--output", help="Exact path of the burned MP4.")
    parser.add_argument("--report", type=Path, help="Exact path of the BURN.json hand-off.")
    parser.add_argument("--preview", action="store_true", help="Render a 540p preview next to the MP4.")
    parser.add_argument("--preview-path", help="Exact path of the 540p preview.")
    parser.add_argument("--poster", action="store_true", help="Render a poster frame next to the MP4.")
    parser.add_argument("--poster-path", help="Exact path of the poster frame.")
    parser.add_argument(
        "--encoder",
        choices=("libx264", "h264_qsv"),
        default=DEFAULT_ENCODER,
        help="FFmpeg H.264 encoder (default: libx264).",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=DEFAULT_CRF,
        help=f"Quality value 1-51 (default: {DEFAULT_CRF}).",
    )
    parser.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        help=f"libx264 preset (default: {DEFAULT_PRESET}).",
    )
    parser.add_argument("--force", action="store_true", help="Re-render even when outputs are fresh.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate and print commands without rendering."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat subtitle cues beyond the video duration as an error.",
    )
    parser.add_argument(
        "--strict-fonts",
        action="store_true",
        help="Fail the burn when libass reports missing glyphs (missing ASS fonts).",
    )
    parser.add_argument(
        "--no-font-check", action="store_true", help="Skip the fc-match font report."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.selection:
            if args.video or args.subtitle:
                raise ValueError("Use either --selection or --video/--subtitle, not both.")
            selection = selection_from_manifest(args.selection)
        else:
            if not args.video or not args.subtitle:
                raise ValueError("Provide --selection, or both --video and --subtitle.")
            selection = selection_from_args(args)
        options = BurnOptions(
            encoder=args.encoder,
            crf=args.crf,
            preset=args.preset,
            force=args.force,
            dry_run=args.dry_run,
            strict=args.strict,
            strict_fonts=args.strict_fonts,
            check_fonts=not args.no_font_check,
            report=args.report,
        )
        process(selection, options)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
