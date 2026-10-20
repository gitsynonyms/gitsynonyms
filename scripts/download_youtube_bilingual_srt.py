#!/usr/bin/env python3
"""Download YouTube English and Chinese captions and merge them into one SRT."""

from __future__ import annotations

import argparse
import html
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

CJK = re.compile(
    r"[\u2e80-\u2fff\u3000-\u30ff\u3100-\u312f\u3130-\u318f"
    r"\u31a0-\u31bf\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\ua960-\ua97f\uac00-\ud7ff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]"
)
CJK_EM, LATIN_EM = 0.85, 0.45
PLAY_RESOLUTION = (1920, 1080)
CAPTION_AREA = (66, 883, 1852, 1022)
STYLE_PRESETS = {
    "classic": {
        "first_font": "Microsoft YaHei",
        "second_font": "Arial",
        "second_font_size": 48,
        "first_font_scale": 1.2,
        "first_color": "#FFC928",
        "second_color": "#FFFFFF",
    },
    "cinema": {
        "first_font": "SimSun",
        "second_font": "Georgia",
        "second_font_size": 48,
        "first_font_scale": 1.2,
        "first_color": "#FFF0D0",
        "second_color": "#FFF0D0",
    },
}
BOX_STYLES = {
    "glass-diamond": {"color": "#708496", "opacity": 0.68, "edge": "#E4F2FF"},
    "presidential-blue": {"color": "#0A3161", "opacity": 0.9, "edge": "#D9E7F5"},
}


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


def ass_escape_text(value: str) -> str:
    tag_pattern = re.compile(
        r"<(?P<simple>/)?(?P<tag>b|i|u|s)>"
        r"|<font\s+color=[\"']?#(?P<color>[0-9a-f]{6})[\"']?>"
        r"|</font>|<br\s*/?>|<[^>]+>",
        re.IGNORECASE,
    )
    style_tags = {"b": "b", "i": "i", "u": "u", "s": "s"}
    parts = []

    def escape_text(text: str) -> str:
        decoded = html.unescape(text)
        return (
            decoded.replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", r"\N")
        )

    cursor = 0
    for match in tag_pattern.finditer(value):
        parts.append(escape_text(value[cursor : match.start()]))
        tag = match.group("tag")
        color = match.group("color")
        if tag:
            command = style_tags[tag.lower()]
            parts.append("{\\" + command + ("0}" if match.group("simple") else "1}"))
        elif color:
            red, green, blue = color[0:2], color[2:4], color[4:6]
            parts.append(r"{\c&H" + blue + green + red + "&}")
        elif match.group(0).lower() == "</font>":
            parts.append(r"{\c}")
        elif match.group(0).lower().startswith("<br"):
            parts.append(r"\N")
        cursor = match.end()
    parts.append(escape_text(value[cursor:]))
    return "".join(parts)


def ass_timestamp(seconds: float) -> str:
    total_centiseconds = round(seconds * 100)
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def ass_color(value: str, *, opacity: int = 100) -> str:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", value.strip())
    if not match:
        raise ValueError(f"ASS color must be a six-digit RGB value such as #FFFFFF: {value!r}")
    if not 0 <= opacity <= 100:
        raise ValueError("ASS opacity must be between 0 and 100.")
    red, green, blue = match.group(1)[0:2], match.group(1)[2:4], match.group(1)[4:6]
    alpha = round(255 * (100 - opacity) / 100)
    return f"&H{alpha:02X}{blue.upper()}{green.upper()}{red.upper()}"


def text_width_units(value: str) -> float:
    plain_text = html.unescape(re.sub(r"<[^>]*>", "", value))
    return sum(CJK_EM if CJK.search(char) else LATIN_EM for char in plain_text)


def fit_font_size(value: str, font_size: float, available_width: int) -> float:
    width = text_width_units(value) * font_size
    return font_size if width <= available_width or width == 0 else round(font_size * available_width / width, 1)


def classify_caption_lines(
    cues: Sequence[dict],
) -> tuple[str, str]:
    chinese_lines = []
    english_lines = []
    for cue in cues:
        for line in cue["text"].splitlines():
            line = line.strip()
            if not line:
                continue
            if CJK.search(html.unescape(re.sub(r"<[^>]*>", "", line))):
                chinese_lines.append(line)
            else:
                english_lines.append(line)
    return "".join(chinese_lines), " ".join(english_lines)


def build_ass(
    first: Sequence[dict],
    second: Sequence[dict],
    *,
    first_font: str = "Microsoft YaHei",
    second_font: str = "Arial",
    second_font_size: int = 48,
    first_font_scale: float = 1.2,
    caption_area: tuple[int, int, int, int] = CAPTION_AREA,
    first_color: str = "#FFC928",
    second_color: str = "#FFFFFF",
    box_color: str = "#000000",
    box_opacity: float = 0.6,
    line_gap: int = 10,
    padding_x: int = 24,
    shadow_color: str = "#000000",
    play_res_x: int = PLAY_RESOLUTION[0],
    play_res_y: int = PLAY_RESOLUTION[1],
    caption_order: str = "chinese-first",
    video_duration: float | None = None,
    box_display: str = "always",
    box_style: str = "glass-diamond",
) -> str:
    if not first or not second:
        raise ValueError("Both subtitle tracks must contain cues.")
    for name, font in (("Chinese", first_font), ("English", second_font)):
        if not font.strip() or any(char in font for char in ",\r\n{}"):
            raise ValueError(f"{name} font name must be non-empty and cannot contain commas or braces.")
    if second_font_size < 1 or not 0.5 <= first_font_scale <= 3:
        raise ValueError("Font size must be positive and Chinese font scale must be between 0.5 and 3.")
    if caption_order not in {"chinese-first", "english-first"}:
        raise ValueError("Caption order must be 'chinese-first' or 'english-first'.")
    if box_display not in {"always", "with-captions"}:
        raise ValueError("Box display must be 'always' or 'with-captions'.")
    if box_style not in BOX_STYLES:
        raise ValueError(f"Unsupported subtitle box style: {box_style!r}")
    if video_duration is not None and (
        not math.isfinite(video_duration) or video_duration < 0
    ):
        raise ValueError("Video duration must be a finite, non-negative number.")
    if not math.isfinite(box_opacity) or not 0 <= box_opacity <= 1:
        raise ValueError("Box opacity must be between 0 and 1.")
    if line_gap < 0 or padding_x < 0:
        raise ValueError("Line gap and horizontal padding must not be negative.")
    if play_res_x < 1 or play_res_y < 1:
        raise ValueError("ASS play resolution must be positive.")
    if len(caption_area) != 4:
        raise ValueError("Caption area must contain left, top, right, and bottom coordinates.")
    area_left, area_top, area_right, area_bottom = caption_area
    if not (
        0 <= area_left < area_right <= play_res_x
        and 0 <= area_top < area_bottom <= play_res_y
    ):
        raise ValueError("Caption area must be a positive rectangle inside the ASS play resolution.")

    first_ass_color = ass_color(first_color)
    second_ass_color = ass_color(second_color)
    shadow_ass_color = ass_color(shadow_color, opacity=35)
    box_ass_color = ass_color(box_color, opacity=round(box_opacity * 100))
    box_edge_color = ass_color(BOX_STYLES[box_style]["edge"], opacity=24)
    facet_color = ass_color("#FFFFFF", opacity=15)
    first_font_size = max(1, round(second_font_size * first_font_scale, 1))
    area_width = area_right - area_left
    area_center_x = (area_left + area_right) // 2
    area_center_y = (area_top + area_bottom) / 2
    available_width = area_width - 2 * padding_x
    if available_width <= 0:
        raise ValueError("Horizontal padding leaves no room for subtitle text.")
    style_format = (
        "Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    styles = (
        f"Style: Chinese,{first_font},{first_font_size:g},{first_ass_color},&H000000FF,"
        f"&H80000000,{shadow_ass_color},-1,0,0,0,100,100,0,0,1,2,1,5,0,0,0,1",
        f"Style: English,{second_font},{second_font_size},{second_ass_color},&H000000FF,"
        f"&H80000000,{shadow_ass_color},0,0,0,0,100,100,0,0,1,2,1,5,0,0,0,1",
        f"Style: CaptionBox,Arial,1,{box_ass_color},&H000000FF,"
        f"&H00000000,{box_edge_color},0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1",
        f"Style: CaptionFacet,Arial,1,{facet_color},&H000000FF,"
        f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
    )
    cues = [*first, *second]
    if any(cue["end"] <= cue["start"] or not cue["text"].strip() for cue in cues):
        raise ValueError("Cannot serialize empty or invalid subtitle cues.")

    boundaries = sorted({cue[key] for cue in cues for key in ("start", "end")})
    last_caption_time = max(cue["end"] for cue in cues)
    box_end = max(last_caption_time, video_duration or 0)
    events = []

    def append_box_events(start: float, end: float) -> None:
        events.append(
            {
                "start": start,
                "end": end,
                "layer": 0,
                "style": "CaptionBox",
                "body": (
                    f"{{\\an7\\pos({area_left},{area_top})\\p1}}"
                    f"m 0 0 l {area_width} 0 {area_width} "
                    f"{area_bottom - area_top} 0 {area_bottom - area_top} 0 0"
                    f"{{\\p0}}"
                ),
            }
        )
        if box_style == "glass-diamond":
            facet_width = max(1, round(area_width * 0.28))
            facet_height = max(1, round((area_bottom - area_top) * 0.42))
            events.append({
                "start": start,
                "end": end,
                "layer": 0,
                "style": "CaptionFacet",
                "body": (
                    f"{{\\an7\\pos({area_left},{area_top})\\p1}}"
                    f"m 0 0 l {facet_width} 0 l {round(facet_width * 0.78)} "
                    f"{facet_height} l 0 {facet_height} 0 0{{\\p0}}"
                ),
            })

    if box_display == "always":
        append_box_events(0, box_end)
    for start, end in zip(boundaries, boundaries[1:]):
        if end <= start:
            continue
        active = sorted(
            (cue for cue in cues if cue["start"] < end and cue["end"] > start),
            key=lambda cue: cue["start"],
        )
        chinese, english = classify_caption_lines(active)
        if not chinese and not english:
            continue
        if box_display == "with-captions":
            append_box_events(start, end)

        cn_size = fit_font_size(chinese, first_font_size, available_width) if chinese else 0
        en_size = fit_font_size(english, second_font_size, available_width) if english else 0
        order = (
            (("english", english, en_size), ("chinese", chinese, cn_size))
            if caption_order == "english-first"
            else (("chinese", chinese, cn_size), ("english", english, en_size))
        )
        visible_lines = [line for line in order if line[1]]
        style = "English" if visible_lines[0][0] == "english" else "Chinese"
        body = ""
        for line_index, (language, text, size) in enumerate(visible_lines):
            if line_index:
                if line_gap:
                    body += r"\N{\fs" + str(line_gap) + r"}\h"
                body += r"\N"
            if language == "english":
                body += r"{\rEnglish"
            elif line_index:
                body += r"{\rChinese"
            else:
                body += "{"
            body += f"\\fs{size:g}" + "}" + ass_escape_text(text)
        body = f"{{\\an5\\pos({area_center_x:g},{area_center_y:g})}}" + body
        event = {
            "start": start,
            "end": end,
            "layer": 1,
            "style": style,
            "body": body,
        }
        if (
            events
            and events[-1]["end"] == start
            and events[-1]["style"] == style
            and events[-1]["body"] == body
        ):
            events[-1]["end"] = end
        else:
            events.append(event)

    events.sort(key=lambda event: (event["start"], event["layer"]))
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {play_res_x}",
            f"PlayResY: {play_res_y}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            f"Format: {style_format}",
            *styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *[
                f"Dialogue: {event['layer']},{ass_timestamp(event['start'])},"
                f"{ass_timestamp(event['end'])},"
                f"{event['style']},,0,0,0,,{event['body']}"
                for event in events
            ],
            "",
        ]
    )


def write_ass(path: Path, content: str) -> None:
    if "[V4+ Styles]" not in content or "[Events]" not in content:
        raise ValueError("Generated ASS is missing styles or events.")
    if not re.search(r"(?m)^Dialogue: ", content):
        raise ValueError("Generated ASS contains no subtitle events.")
    path.write_text(content, encoding="utf-8-sig", newline="")


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
    javascript_runtime = ["--js-runtimes", "deno"] if shutil.which("deno") else []
    try:
        return subprocess.run(
            [executable, *javascript_runtime, *args],
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


def download_video(url: str, video_id: str, directory: Path) -> Path:
    print("  Downloading source video on the GitHub runner for subtitle burn-in...", flush=True)
    run_ytdlp(
        [
            "--no-warnings",
            "--retries",
            "5",
            "--merge-output-format",
            "mp4",
            "-f",
            "bv*+ba/b",
            "-o",
            str(directory / f"{video_id}.source.%(ext)s"),
            url,
        ],
        show_progress=True,
    )
    matches = [
        path
        for path in directory.glob(f"{video_id}.source.*")
        if path.is_file() and not path.name.endswith((".part", ".ytdl"))
    ]
    if len(matches) != 1:
        raise RuntimeError(f"yt-dlp did not produce exactly one source video for {video_id}.")
    return matches[0]


def burn_ass_into_video(
    video: Path,
    ass_path: Path,
    output_path: Path,
    *,
    video_encoder: str = "libx264",
    global_quality: int = 18,
) -> None:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is required for burn-in but is not available on PATH.")
    if video_encoder not in {"h264_qsv", "libx264"}:
        raise ValueError("Video encoder must be 'h264_qsv' or 'libx264'.")
    if global_quality < 1 or global_quality > 51:
        raise ValueError("Global quality must be between 1 and 51.")
    filter_path = str(ass_path.resolve()).replace("\\", "/")
    filter_path = (
        filter_path.replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )
    quality_option = "-global_quality" if video_encoder == "h264_qsv" else "-crf"
    command = [
        executable,
        "-y",
        "-i",
        str(video),
        "-vf",
        f"subtitles=filename='{filter_path}'",
        "-c:v",
        video_encoder,
        quality_option,
        str(global_quality),
        "-c:a",
        "copy",
        str(output_path),
    ]
    print("  Rendering the video with permanently burned-in ASS subtitles...", flush=True)
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as error:
        raise RuntimeError("ffmpeg is required for burn-in but is not available on PATH.") from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or str(error)).strip()
        raise RuntimeError(f"ffmpeg failed to burn in subtitles: {details}") from error


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
    parser.add_argument(
        "--style-preset",
        choices=tuple(STYLE_PRESETS),
        default="classic",
        help="ASS style preset: classic Chinese-first or cinema sample-like English-first.",
    )
    parser.add_argument("--chinese-font", help="ASS font family for the Chinese line.")
    parser.add_argument("--english-font", help="ASS font family for the English line.")
    parser.add_argument(
        "--english-font-size",
        type=int,
        help="English ASS font size; Chinese size is derived from this value and --chinese-font-scale.",
    )
    parser.add_argument(
        "--chinese-font-scale",
        type=float,
        help="Chinese font size as a multiple of the English font size (default: 1.2).",
    )
    parser.add_argument("--chinese-color", help="Chinese ASS text color in #RRGGBB format.")
    parser.add_argument("--english-color", help="English ASS text color in #RRGGBB format.")
    parser.add_argument(
        "--box-display",
        choices=("always", "with-captions"),
        default="always",
        help="Show the box throughout the video or only while subtitle text is active.",
    )
    parser.add_argument(
        "--box-style",
        choices=tuple(BOX_STYLES),
        default="glass-diamond",
        help="Background appearance: translucent glass-diamond or presidential blue.",
    )
    parser.add_argument("--box-color", help="Override the selected ASS box color in #RRGGBB format.")
    parser.add_argument(
        "--box-opacity",
        type=float,
        help="Override box opacity from 0 (transparent) to 1 (opaque).",
    )
    parser.add_argument("--box-x", type=int, default=66, help="Caption-box left coordinate on the 1920x1080 canvas.")
    parser.add_argument("--box-y", type=int, default=883, help="Caption-box top coordinate on the 1920x1080 canvas.")
    parser.add_argument("--box-width", type=int, default=1786, help="Caption-box width on the 1920x1080 canvas.")
    parser.add_argument("--box-height", type=int, default=139, help="Caption-box height on the 1920x1080 canvas.")
    parser.add_argument(
        "--burn-in",
        action="store_true",
        help="Download and keep the source video, then create an MP4 with the ASS subtitles burned in.",
    )
    parser.add_argument(
        "--video-encoder",
        choices=("h264_qsv", "libx264"),
        default="libx264",
        help="FFmpeg H.264 encoder for burn-in (default: libx264; use h264_qsv for Intel Quick Sync).",
    )
    parser.add_argument(
        "--global-quality",
        type=int,
        default=18,
        help="FFmpeg quality value (1-51); with h264_qsv this is passed as -global_quality.",
    )
    args = parser.parse_args(argv)
    if args.sleep_subtitles < 0:
        parser.error("--sleep-subtitles must not be negative")
    languages = [language.strip() for language in args.languages.split(",")]
    if len(languages) != 2 or not all(languages) or languages[0].lower() == languages[1].lower():
        parser.error("--languages must contain two distinct comma-separated language codes")
    preset = STYLE_PRESETS[args.style_preset]
    caption_area = (
        args.box_x,
        args.box_y,
        args.box_x + args.box_width,
        args.box_y + args.box_height,
    )

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
            f"  Source tracks: {first_code} + {second_code}\n"
            f"  ASS style preset: {args.style_preset}",
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
        ass_output = folder / f"{combined_kind}_Bilingual_{first_code}-{second_code}.ass"
        write_ass(
            ass_output,
            build_ass(
                first,
                second,
                first_font=args.chinese_font or preset["first_font"],
                second_font=args.english_font or preset["second_font"],
                second_font_size=args.english_font_size or preset["second_font_size"],
                first_font_scale=args.chinese_font_scale or preset["first_font_scale"],
                first_color=args.chinese_color or preset["first_color"],
                second_color=args.english_color or preset["second_color"],
                box_color=args.box_color or BOX_STYLES[args.box_style]["color"],
                box_opacity=(
                    BOX_STYLES[args.box_style]["opacity"]
                    if args.box_opacity is None
                    else args.box_opacity
                ),
                caption_area=caption_area,
                caption_order="english-first" if args.style_preset == "cinema" else "chinese-first",
                video_duration=metadata.get("duration"),
                box_display=args.box_display,
                box_style=args.box_style,
            ),
        )
        print("Step 5/6: Validating generated subtitle files...", flush=True)
        cue_count = validate_srt(output.read_text(encoding="utf-8-sig"))
        print(f"  OK: {output.name} ({cue_count} cues)", flush=True)
        print(f"  OK: {ass_output.name} (ASS styles: Chinese above English)", flush=True)
        if args.burn_in:
            print("Step 6/6: Downloading the video and burning in the ASS subtitles...", flush=True)
            source_video = download_video(args.url, video_id, folder)
            burned_output = folder / f"Burned_Bilingual_{video_id}.mp4"
            burn_ass_into_video(
                source_video,
                ass_output,
                burned_output,
                video_encoder=args.video_encoder,
                global_quality=args.global_quality,
            )
            print(f"  OK: {burned_output.name}", flush=True)
        else:
            print("Step 6/6: Video burn-in skipped; pass --burn-in to render a subtitled MP4.", flush=True)
        print("Original downloaded subtitles were left untouched:", flush=True)
        print(first_path.resolve())
        print(second_path.resolve())
        print("Done. Subtitle files:", flush=True)
        print(output.resolve())
        print(ass_output.resolve())
        if args.burn_in:
            print(burned_output.resolve())
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
