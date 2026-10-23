#!/usr/bin/env python3
"""Download YouTube English and Chinese captions and merge them into one SRT."""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
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
    "presidential-blue": {"color": "#0F1647", "opacity": 0.9, "edge": "#D9E7F5"},
    "solid-color": {"color": "#708496", "opacity": 1, "edge": "#D9E7F5"},
}
ENGLISH_HIGHLIGHT_COLOR = "#00D7FF"
# YouTube rounds rolling-caption boundary timestamps to the millisecond, so an
# inline word timestamp can land a few milliseconds outside its own cue.
INLINE_TIMESTAMP_TOLERANCE = 0.5


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


def vtt_timestamp_to_seconds(value: str) -> float:
    match = re.fullmatch(r"(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})", value)
    if not match:
        raise ValueError(f"Invalid WebVTT timestamp: {value!r}")
    hours, minutes, seconds, milliseconds = match.groups()
    result = (
        int(hours or 0) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000
    )
    if int(minutes) > 59 or int(seconds) > 59 or not math.isfinite(result):
        raise ValueError(f"Invalid WebVTT timestamp: {value!r}")
    return result


def parse_vtt(content: str) -> list[dict]:
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    timestamp_pattern = r"(?:\d{2,}:)?\d{2}:\d{2}\.\d{3}"
    timing_line = re.compile(
        rf"^\s*({timestamp_pattern})\s+-->\s+({timestamp_pattern})(?:\s+.*)?$"
    )
    inline_timestamp = re.compile(rf"<({timestamp_pattern})>")
    result = []
    lines = normalized.split("\n")
    index = 0
    while index < len(lines):
        match = timing_line.match(lines[index])
        if not match:
            index += 1
            continue
        start = vtt_timestamp_to_seconds(match.group(1))
        end = vtt_timestamp_to_seconds(match.group(2))
        if end <= start:
            raise ValueError(f"WebVTT cue end must be after its start: {lines[index]!r}")
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines) or timing_line.match(lines[index]):
            continue
        body_lines = []
        while index < len(lines) and lines[index].strip():
            body_lines.append(lines[index])
            index += 1

        chunks: list[tuple[float, str]] = []
        current_start = start
        has_inline_timing = False
        for line_index, line in enumerate(body_lines):
            if line_index:
                chunks.append((current_start, "\n"))
            position = 0
            for stamp in inline_timestamp.finditer(line):
                chunks.append((current_start, line[position:stamp.start()]))
                stamp_seconds = vtt_timestamp_to_seconds(stamp.group(1))
                if not start <= stamp_seconds < end:
                    # YouTube's rolling captions can round a boundary timestamp a
                    # few milliseconds past the cue end (or before its start).
                    # Snap that stamp to the cue boundary instead of rejecting the
                    # whole track; keep failing on timestamps further outside.
                    if (
                        start - INLINE_TIMESTAMP_TOLERANCE
                        <= stamp_seconds
                        <= end + INLINE_TIMESTAMP_TOLERANCE
                    ):
                        stamp_seconds = min(max(stamp_seconds, start), end)
                    else:
                        raise ValueError(
                            f"Inline WebVTT timestamp is outside its cue: {stamp.group(0)!r}"
                        )
                current_start = stamp_seconds
                has_inline_timing = True
                position = stamp.end()
            chunks.append((current_start, line[position:]))

        cleaned_chunks = []
        for chunk_start, chunk_text in chunks:
            text = html.unescape(chunk_text)
            text = re.sub(r"</?(?:v|lang|c(?:\.[\w-]+)*)(?:\s+[^>]*)?>", "", text)
            text = re.sub(r"<(?:ruby|rt)(?:\s+[^>]*)?>|</(?:ruby|rt)>", "", text)
            text = re.sub(r"<[^>]*>", lambda tag: tag.group(0) if tag.group(0).lower() in {
                "<b>", "</b>", "<i>", "</i>", "<u>", "</u>"
            } else "", text)
            cleaned_chunks.append((chunk_start, text))
        cue_text = "".join(text for _, text in cleaned_chunks).strip()
        if not cue_text:
            continue

        cue = {
            "start": start,
            "end": end,
            "text": cue_text,
            "start_timestamp": match.group(1),
            "end_timestamp": match.group(2),
        }
        if has_inline_timing:
            words = []
            for chunk_index, (chunk_start, text) in enumerate(cleaned_chunks):
                chunk_end = (
                    cleaned_chunks[chunk_index + 1][0]
                    if chunk_index + 1 < len(cleaned_chunks)
                    else end
                )
                tokens = _rolling_word_spans(text)
                for token_index, token in enumerate(tokens):
                    token_start = chunk_start + (
                        (chunk_end - chunk_start) * token_index / len(tokens)
                        if tokens
                        else 0
                    )
                    token_end = chunk_start + (
                        (chunk_end - chunk_start) * (token_index + 1) / len(tokens)
                        if tokens
                        else 0
                    )
                    words.append({
                        "start": token_start,
                        "end": token_end,
                        "text": token.group(),
                    })
            cue["word_timings"] = words
        result.append(cue)
    if not result:
        raise ValueError("Subtitle file has no valid WebVTT cues.")
    return result


def _word_timings_from_segments(
    text: str, segments: Sequence[tuple[float, float, str]]
) -> list[dict]:
    """Map caption segments onto the words of a cue.

    ``segments`` holds (start, end, text) fragments in caption order. Every word
    of the cue text is placed inside the segment that produced it, and words
    sharing one segment are spread evenly inside that segment, which keeps the
    karaoke highlight aligned with the spoken word.
    """
    words = list(_rolling_word_spans(text))
    if not words or not segments:
        return []
    segment_spans: list[tuple[float, float, int, int]] = []
    offset = 0
    for segment_start, segment_end, segment_text in segments:
        length = len(segment_text)
        segment_spans.append((segment_start, segment_end, offset, offset + length))
        offset += length
    grouped: dict[int, list[int]] = {}
    for word_index, word in enumerate(words):
        for segment_index, (_, _, begin, finish) in enumerate(segment_spans):
            if word.start() >= begin and word.start() < finish:
                grouped.setdefault(segment_index, []).append(word_index)
                break
        else:
            grouped.setdefault(len(segment_spans) - 1, []).append(word_index)
    timings: list[dict] = [None] * len(words)  # type: ignore[list-item]
    for segment_index, word_indexes in grouped.items():
        segment_start, segment_end, begin, finish = segment_spans[segment_index]
        span = max(finish - begin, 1)
        total = max(segment_end - segment_start, 0.0)
        if len(word_indexes) == 1:
            timings[word_indexes[0]] = {
                "start": segment_start,
                "end": segment_end,
                "text": words[word_indexes[0]].group(),
            }
            continue
        for word_index in word_indexes:
            word = words[word_index]
            timings[word_index] = {
                "start": segment_start + total * (word.start() - begin) / span,
                "end": segment_start + total * (word.end() - begin) / span,
                "text": word.group(),
            }
    return [timing for timing in timings if timing is not None]


def parse_json3(content: str) -> list[dict]:
    """Parse YouTube's JSON3 caption format, which carries per-word offsets."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON3 caption file: {error}") from error
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError("JSON3 caption file has no events.")
    result = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segments = event.get("segs")
        if not isinstance(segments, list) or not segments:
            continue
        try:
            start = float(event.get("tStartMs", 0)) / 1000
            end = start + float(event.get("dDurationMs", 0)) / 1000
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        starts: list[float] = []
        parts: list[str] = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("utf8", ""))
            if not text:
                continue
            try:
                offset = float(segment.get("tOffsetMs", 0)) / 1000
            except (TypeError, ValueError):
                offset = 0.0
            starts.append(start + offset)
            parts.append(text)
        if not parts:
            continue
        text = "".join(parts)
        if not text.strip():
            continue
        timed_segments = [
            (
                starts[index],
                starts[index + 1] if index + 1 < len(starts) else end,
                parts[index],
            )
            for index in range(len(parts))
        ]
        cue = {
            "start": start,
            "end": end,
            "text": text,
            "start_timestamp": format_timestamp(start),
            "end_timestamp": format_timestamp(end),
        }
        words = _word_timings_from_segments(text, timed_segments)
        if words:
            cue["word_timings"] = words
        result.append(cue)
    if not result:
        raise ValueError("JSON3 caption file has no valid cues.")
    return result


def parse_srv3(content: str) -> list[dict]:
    """Parse YouTube's srv3 caption format, which carries per-word starts."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"Invalid srv3 caption file: {error}") from error
    result = []
    for paragraph in root.iter():
        if paragraph.tag.split("}")[-1] != "p":
            continue
        try:
            start = float(paragraph.get("t", 0)) / 1000
            end = start + float(paragraph.get("d", 0)) / 1000
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        starts: list[float] = []
        parts: list[str] = []
        for child in paragraph:
            if child.tag.split("}")[-1] != "s":
                continue
            text = "".join(child.itertext())
            if not text:
                continue
            try:
                # srv3 timestamps on <s> are offsets from the paragraph start.
                child_start = start + float(child.get("t", 0)) / 1000
            except (TypeError, ValueError):
                child_start = start
            starts.append(child_start)
            parts.append(text)
        if not parts:
            continue
        text = "".join(parts)
        if not text.strip():
            continue
        timed_segments = [
            (
                starts[index],
                starts[index + 1] if index + 1 < len(starts) else end,
                parts[index],
            )
            for index in range(len(parts))
        ]
        cue = {
            "start": start,
            "end": end,
            "text": text,
            "start_timestamp": format_timestamp(start),
            "end_timestamp": format_timestamp(end),
        }
        words = _word_timings_from_segments(text, timed_segments)
        if words:
            cue["word_timings"] = words
        result.append(cue)
    if not result:
        raise ValueError("srv3 caption file has no valid cues.")
    return result


def parse_srv2(content: str) -> list[dict]:
    """Parse YouTube's srv2 caption format, a flat list of timed text runs.

    A run without ``append`` starts a caption; the following ``append`` runs
    extend it. Each run carries its own start, so the words of a caption keep
    the timing YouTube reported for them.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"Invalid srv2 caption file: {error}") from error
    runs: list[dict] = []
    for node in root.iter():
        if node.tag.split("}")[-1] != "text":
            continue
        try:
            start = float(node.get("t", 0)) / 1000
            duration = float(node.get("d", 0)) / 1000
        except (TypeError, ValueError):
            continue
        text = "".join(node.itertext())
        runs.append(
            {
                "start": start,
                "end": start + duration,
                "text": text,
                "append": node.get("append") == "1",
            }
        )
    result = []
    group: list[dict] = []
    for run in runs:
        if not run["append"] and group:
            result.append(_caption_from_runs(group))
            group = []
        group.append(run)
    if group:
        result.append(_caption_from_runs(group))
    if not result:
        raise ValueError("srv2 caption file has no valid cues.")
    return [cue for cue in result if cue is not None]


def _caption_from_runs(runs: Sequence[dict]) -> dict | None:
    """Build one caption from the timed runs that make it up."""
    if not runs:
        return None
    start = runs[0]["start"]
    end = runs[0]["end"]
    text = "".join(run["text"] for run in runs)
    if not text.strip():
        return None
    segments: list[tuple[float, float, str]] = []
    for index, run in enumerate(runs):
        if not run["text"].strip():
            continue
        following = next(
            (later["start"] for later in runs[index + 1:] if later["text"].strip()),
            end,
        )
        segments.append((run["start"], min(following, end), run["text"]))
    cue = {
        "start": start,
        "end": end,
        "text": text,
        "start_timestamp": format_timestamp(start),
        "end_timestamp": format_timestamp(end),
    }
    words = _word_timings_from_segments(text, segments)
    if words:
        cue["word_timings"] = words
    return cue


def parse_srv1(content: str) -> list[dict]:
    """Parse YouTube's srv1 caption format."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"Invalid srv1 caption file: {error}") from error
    result = []
    for node in root.iter():
        if node.tag.split("}")[-1] != "text":
            continue
        try:
            start = float(node.get("start", 0))
            end = start + float(node.get("dur", 0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        starts: list[float] = []
        parts: list[str] = []
        for child in node:
            if child.tag.split("}")[-1] != "p":
                continue
            text = "".join(child.itertext())
            if not text:
                continue
            try:
                child_start = float(child.get("t", 0)) / 1000
            except (TypeError, ValueError):
                child_start = start
            starts.append(child_start)
            parts.append(text)
        text = "".join(parts) if parts else "".join(node.itertext())
        if not text.strip():
            continue
        timed_segments = [
            (
                starts[index],
                starts[index + 1] if index + 1 < len(starts) else end,
                parts[index],
            )
            for index in range(len(parts))
        ]
        cue = {
            "start": start,
            "end": end,
            "text": text,
            "start_timestamp": format_timestamp(start),
            "end_timestamp": format_timestamp(end),
        }
        words = _word_timings_from_segments(text, timed_segments)
        if words:
            cue["word_timings"] = words
        result.append(cue)
    if not result:
        raise ValueError("srv1 caption file has no valid cues.")
    return result


def ttml_timestamp_to_seconds(value: str) -> float:
    """Convert a TTML offset such as ``00:00:01.250`` or ``1.25s`` to seconds."""
    cleaned = value.strip()
    if cleaned.endswith("s"):
        cleaned = cleaned[:-1]
    parts = cleaned.split(":")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as error:
        raise ValueError(f"Invalid TTML timestamp: {value!r}") from error
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"Invalid TTML timestamp: {value!r}")
    return seconds


def parse_ttml(content: str) -> list[dict]:
    """Parse a TTML caption file; its timing is cue level only."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError(f"Invalid TTML caption file: {error}") from error
    result = []
    for node in root.iter():
        if node.tag.split("}")[-1] != "p":
            continue
        begin = node.get("begin")
        end = node.get("end")
        if not begin or not end:
            continue
        try:
            start = ttml_timestamp_to_seconds(begin)
            end_value = ttml_timestamp_to_seconds(end)
        except ValueError:
            continue
        if end_value <= start:
            continue
        text = "".join(node.itertext())
        if not text.strip():
            continue
        result.append(
            {
                "start": start,
                "end": end_value,
                "text": text,
                "start_timestamp": format_timestamp(start),
                "end_timestamp": format_timestamp(end_value),
            }
        )
    if not result:
        raise ValueError("TTML caption file has no valid cues.")
    return result


CAPTION_FORMATS = ("vtt", "srt", "ttml", "srv3", "srv2", "srv1", "json3")
CAPTION_PARSERS = {
    "json3": parse_json3,
    "srv3": parse_srv3,
    "srv2": parse_srv2,
    "srv1": parse_srv1,
    "vtt": parse_vtt,
    "ttml": parse_ttml,
    "srt": parse_srt,
}
# Formats ordered from the most precise timing data to the least precise.
CAPTION_PRECISION_ORDER = ("json3", "srv3", "srv2", "srv1", "vtt", "ttml", "srt")


def parse_caption_file(path: Path) -> list[dict]:
    """Parse a downloaded caption file with the parser for its format."""
    extension = path.suffix.lower().lstrip(".")
    parser = CAPTION_PARSERS.get(extension)
    if parser is None:
        raise ValueError(f"Unsupported caption format: {path.name}")
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    return parser(content)


def select_most_precise_cues(files: Sequence[Path]) -> tuple[list[dict], Path]:
    """Pick the downloaded caption file that carries the most precise timing."""
    by_extension: dict[str, Path] = {}
    for path in files:
        extension = path.suffix.lower().lstrip(".")
        if extension in CAPTION_PARSERS and extension not in by_extension:
            by_extension[extension] = path
    for extension in CAPTION_PRECISION_ORDER:
        path = by_extension.get(extension)
        if path is None:
            continue
        try:
            cues = parse_caption_file(path)
        except ValueError:
            continue
        if cues:
            return cues, path
    raise RuntimeError("No downloaded caption file could be parsed.")


def _without_rolling_cues(cues: Sequence[dict]) -> list[dict]:
    ordered = sorted(cues, key=lambda cue: cue["start"])
    result = []
    for index, cue in enumerate(ordered):
        end = min(
            cue["end"],
            ordered[index + 1]["start"] if index + 1 < len(ordered) else cue["end"],
        )
        if end > cue["start"]:
            result.append({**cue, "end": end})
    return result


def _single_caption_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return text
    contains_cjk = any(
        CJK.search(html.unescape(re.sub(r"<[^>]*>", "", line)))
        for line in lines
    )
    separator = "" if contains_cjk else " "
    return separator.join(lines)


def _pair_same_end_cues(
    first: Sequence[dict], second: Sequence[dict]
) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    second_by_end: dict[int, list[tuple[int, dict]]] = {}
    for index, cue in enumerate(second):
        end_ms = round(cue["end"] * 1000)
        second_by_end.setdefault(end_ms, []).append((index, cue))

    pairs = []
    paired_first = set()
    paired_second = set()
    for first_index, cue in enumerate(first):
        candidates = second_by_end.get(round(cue["end"] * 1000), [])
        candidates = [item for item in candidates if item[0] not in paired_second]
        if candidates:
            second_index, match = min(
                candidates, key=lambda item: abs(item[1]["start"] - cue["start"])
            )
            pairs.append((cue, match))
            paired_first.add(first_index)
            paired_second.add(second_index)

    return (
        pairs,
        [cue for index, cue in enumerate(first) if index not in paired_first],
        [cue for index, cue in enumerate(second) if index not in paired_second],
    )


def align_and_merge(
    first: Sequence[dict],
    second: Sequence[dict],
) -> str:
    if not first or not second:
        raise ValueError("Both subtitle tracks must contain cues.")
    first = _without_rolling_cues(first)
    second = _without_rolling_cues(second)
    pairs, unmatched_first, unmatched_second = _pair_same_end_cues(first, second)
    cues = [*first, *second]
    if any(cue["end"] <= cue["start"] or not cue["text"].strip() for cue in cues):
        raise ValueError("Cannot serialize empty or invalid subtitle cues.")

    boundaries = sorted({
        cue[key]
        for cue in (*unmatched_first, *unmatched_second)
        for key in ("start", "end")
    })
    segments = [
        {
            "start": min(first_cue["start"], second_cue["start"]),
            "end": first_cue["end"],
            "top": _single_caption_line(first_cue["text"]),
            "bottom": _single_caption_line(second_cue["text"]),
        }
        for first_cue, second_cue in pairs
    ]
    timeline_segments = []
    for start, end in zip(boundaries, boundaries[1:]):
        top_cues = sorted(
            (
                cue
                for cue in unmatched_first
                if cue["start"] < end and cue["end"] > start
            ),
            key=lambda cue: cue["start"],
        )
        bottom_cues = sorted(
            (cue for cue in unmatched_second if cue["start"] < end and cue["end"] > start),
            key=lambda cue: cue["start"],
        )
        top = _single_caption_line("\n".join(cue["text"] for cue in top_cues))
        bottom = _single_caption_line("\n".join(cue["text"] for cue in bottom_cues))
        if not top and not bottom:
            continue
        if timeline_segments and timeline_segments[-1]["end"] == start and (
            timeline_segments[-1]["top"], timeline_segments[-1]["bottom"]
        ) == (top, bottom):
            timeline_segments[-1]["end"] = end
        else:
            timeline_segments.append(
                {"start": start, "end": end, "top": top, "bottom": bottom}
            )

    segments.extend(timeline_segments)
    segments.sort(key=lambda segment: (segment["start"], segment["end"]))
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


def ass_karaoke_text(
    value: str,
    duration: float,
    *,
    start: float = 0,
    fallback_start: float | None = None,
    word_timings: Sequence[dict | None] | None = None,
) -> str:
    body = ass_escape_text(value)
    word_spans = []
    cursor = 0
    override_tag = re.compile(r"(?<!\\)\{[^}]*\}")
    for tag in override_tag.finditer(body):
        word_spans.extend(
            (cursor + word.start(), cursor + word.end())
            for word in _rolling_word_spans(body[cursor : tag.start()])
        )
        cursor = tag.end()
    word_spans.extend(
        (cursor + word.start(), cursor + word.end())
        for word in _rolling_word_spans(body[cursor:])
    )
    if not word_spans:
        return body

    total_centiseconds = max(0, round(duration * 100))
    fallback_centiseconds = round(
        max(0, min(duration, (fallback_start if fallback_start is not None else start) - start))
        * 100
    )
    fallback_duration = total_centiseconds - fallback_centiseconds
    starts = [
        fallback_centiseconds
        + round(fallback_duration * index / len(word_spans))
        for index in range(len(word_spans))
    ]
    if word_timings:
        timings = list(word_timings[:len(starts)])
        timings.extend([None] * (len(starts) - len(timings)))
        for index, timing in enumerate(timings):
            if timing is not None:
                starts[index] = round(
                    max(0, min(duration, timing["start"] - start)) * 100
                )
        known = [
            index for index, timing in enumerate(timings) if timing is not None
        ]
        if known:
            for index in range(len(starts)):
                if timings[index] is not None:
                    continue
                before = [known_index for known_index in known if known_index < index]
                after = [known_index for known_index in known if known_index > index]
                if before and after:
                    left, right = before[-1], after[0]
                    starts[index] = round(
                        starts[left]
                        + (starts[right] - starts[left]) * (index - left) / (right - left)
                    )
                elif after:
                    right = after[0]
                    starts[index] = fallback_centiseconds + round(
                        (starts[right] - fallback_centiseconds)
                        * (index + 1)
                        / (right + 1)
                    )
                elif before:
                    left = before[-1]
                    remaining = len(starts) - 1 - left
                    starts[index] = (
                        starts[left]
                        + round((total_centiseconds - starts[left]) * (index - left) / remaining)
                        if remaining
                        else starts[left]
                    )
    starts = [
        max(0, min(total_centiseconds, start_time))
        for start_time in starts
    ]
    for index in range(1, len(starts)):
        starts[index] = max(starts[index], starts[index - 1])

    result = []
    cursor = 0
    for index, (start, end) in enumerate(word_spans):
        word_start = starts[index]
        next_start = starts[index + 1] if index + 1 < len(starts) else total_centiseconds
        word_duration = max(0, next_start - word_start)
        result.append(body[cursor:start])
        result.append(f"{{\\kt{word_start}\\k{word_duration}}}")
        result.append(body[start:end])
        cursor = end
    result.append(body[cursor:])
    return "".join(result)


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
    entries: dict[str, list[tuple[dict, str]]] = {"chinese": [], "english": []}
    for cue in cues:
        for line in cue["text"].splitlines():
            line = line.strip()
            if not line:
                continue
            language = (
                "chinese"
                if CJK.search(html.unescape(re.sub(r"<[^>]*>", "", line)))
                else "english"
            )
            entries[language].append((cue, line))
    chinese = _combine_caption_entries(entries["chinese"], separator="")
    english = _combine_caption_entries(entries["english"], separator=" ")
    return chinese, english


def _remove_rolling_prefix(previous: str, current: str) -> str:
    if not previous or not current or "<" in previous or "<" in current:
        return current
    previous = re.sub(r"\s+", " ", html.unescape(previous)).strip()
    current = re.sub(r"\s+", " ", html.unescape(current)).strip()
    if CJK.search(previous + current):
        max_overlap = min(len(previous), len(current))
        for length in range(max_overlap, 1, -1):
            if previous[-length:] == current[:length]:
                return current[length:].lstrip()
        return current

    previous_words = list(re.finditer(r"[\w’'-]+", previous))
    current_words = list(re.finditer(r"[\w’'-]+", current))
    max_words = min(len(previous_words), len(current_words))
    for count in range(max_words, 1, -1):
        previous_suffix = [word.group().casefold() for word in previous_words[-count:]]
        current_prefix = [word.group().casefold() for word in current_words[:count]]
        if previous_suffix == current_prefix:
            return current[current_words[count - 1].end():].lstrip(" \t,.;:!?-")
    return current


def _append_rolling_text(previous: str, current: str, *, separator: str) -> str:
    if not previous:
        return current
    previous_plain = re.sub(r"\s+", " ", html.unescape(previous)).strip()
    current_plain = re.sub(r"\s+", " ", html.unescape(current)).strip()
    if not previous_plain or not current_plain:
        return current if current_plain else previous
    previous_folded = previous_plain.casefold()
    current_folded = current_plain.casefold()
    if current_folded.startswith(previous_folded):
        return current
    if previous_folded.startswith(current_folded):
        return previous
    remainder = _remove_rolling_prefix(previous, current)
    if remainder != current:
        return previous + (separator if separator and remainder else "") + remainder
    return previous + separator + current


def _combine_caption_entries(
    entries: Sequence[tuple[dict, str]], *, separator: str
) -> str:
    value = ""
    for _, text in entries:
        value = _append_rolling_text(value, text, separator=separator)
    return value


def _rolling_word_spans(text: str) -> list[re.Match[str]]:
    plain_text = html.unescape(re.sub(r"<[^>]*>", "", text))
    if CJK.search(plain_text):
        return list(re.finditer(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", plain_text))
    return list(
        re.finditer(
            r"(?:[A-Z]\.){2,}|[\w]+(?:[’'-][\w]+)*(?:\s+[A-Z]\.)*",
            plain_text,
        )
    )


def _word_timings_for_text(
    text: str, cues: Sequence[dict]
) -> list[dict | None]:
    target_words = _rolling_word_spans(text)
    target_tokens = [word.group().casefold() for word in target_words]
    timings: list[dict | None] = [None] * len(target_words)
    for cue in cues:
        cue_timings = cue.get("word_timings", [])
        if not cue_timings:
            continue
        cue_words = _rolling_word_spans(cue["text"])
        cue_tokens = [word.group().casefold() for word in cue_words]
        matcher = SequenceMatcher(None, target_tokens, cue_tokens, autojunk=False)
        for target_index, cue_index, size in matcher.get_matching_blocks():
            for offset in range(size):
                timing_index = cue_index + offset
                if timing_index >= len(cue_timings):
                    continue
                target_word_index = target_index + offset
                candidate = cue_timings[timing_index]
                previous = timings[target_word_index]
                if previous is None or candidate["start"] < previous["start"]:
                    timings[target_word_index] = candidate
    return timings


def _vtt_word_cut(
    text: str, cues: Sequence[dict], boundary: float
) -> int | None:
    full_words = _rolling_word_spans(text)
    if not full_words:
        return None
    full_tokens = [word.group().casefold() for word in full_words]
    timed_starts: dict[int, float] = {}
    for cue in cues:
        cue_timings = cue.get("word_timings", [])
        if not cue_timings:
            continue
        cue_words = _rolling_word_spans(cue["text"])
        cue_tokens = [word.group().casefold() for word in cue_words]
        matcher = SequenceMatcher(None, full_tokens, cue_tokens, autojunk=False)
        for full_index, cue_index, size in matcher.get_matching_blocks():
            for offset in range(size):
                token_index = full_index + offset
                timing_index = cue_index + offset
                if timing_index < len(cue_timings):
                    timed_starts[token_index] = min(
                        timed_starts.get(token_index, cue_timings[timing_index]["start"]),
                        cue_timings[timing_index]["start"],
                    )
    if not timed_starts:
        return None
    last_visible = max(
        (index for index, start in timed_starts.items() if start < boundary),
        default=-1,
    )
    return min(len(full_words), last_visible + 1)


def _rolling_delta(previous: str, current: str, *, allow_single_word: bool) -> str:
    if not previous or not current or "<" in previous or "<" in current:
        return current
    previous_plain = re.sub(r"\s+", " ", html.unescape(previous)).strip()
    current_plain = re.sub(r"\s+", " ", html.unescape(current)).strip()
    if CJK.search(previous_plain + current_plain):
        min_overlap = 1 if allow_single_word else 2
        max_overlap = min(len(previous_plain), len(current_plain))
        for length in range(max_overlap, min_overlap - 1, -1):
            if previous_plain[-length:] == current_plain[:length]:
                return current_plain[length:].lstrip(" \t，。！？、；：,.!?;:-")
        return current

    previous_words = _rolling_word_spans(previous_plain)
    current_words = _rolling_word_spans(current_plain)
    if len(previous_words) < 2 or not current_words:
        return current
    previous_tokens = [word.group().casefold() for word in previous_words]
    current_tokens = [word.group().casefold() for word in current_words]
    if (
        len(previous_tokens) <= len(current_tokens)
        and current_tokens[:len(previous_tokens)] == previous_tokens
    ):
        return current_plain[current_words[len(previous_tokens) - 1].end():].lstrip(
            " \t,.;:!?-"
        )

    min_overlap = 1 if allow_single_word else 2
    max_overlap = min(len(previous_tokens), len(current_tokens))
    for count in range(max_overlap, min_overlap - 1, -1):
        if previous_tokens[-count:] == current_tokens[:count]:
            return current_plain[current_words[count - 1].end():].lstrip(
                " \t,.;:!?-"
            )
    return current


def _rolling_cue_groups(cues: Sequence[dict]) -> list[list[dict]]:
    ordered = sorted(cues, key=lambda cue: (cue["start"], cue["end"]))
    rolling_groups = []
    current_group = []

    for cue in ordered:
        if current_group:
            previous = current_group[-1]
            current_text = _single_caption_line(cue["text"])
            previous_text = _single_caption_line(previous["text"])
            sample = re.sub(r"<[^>]*>", "", previous_text + current_text)
            separator = "" if CJK.search(html.unescape(sample)) else " "
            same_end = any(
                round(prior["end"] * 1000) == round(cue["end"] * 1000)
                for prior in current_group
            )
            temporally_contiguous = cue["start"] <= max(
                prior["end"] for prior in current_group
            ) + 0.25
            rolling = _rolling_delta(
                previous_text,
                current_text,
                allow_single_word=len(current_group) > 1,
            ) != current_text
            if same_end or (temporally_contiguous and rolling):
                current_group.append(cue)
                continue
            if len(current_group) > 1:
                rolling_groups.append(current_group)
            current_group = []
        current_group.append(cue)
    if len(current_group) > 1:
        rolling_groups.append(current_group)
    return rolling_groups


def _cue_group_signature(group: Sequence[dict]) -> tuple[tuple[int, int], ...]:
    return tuple(
        (round(cue["start"] * 1000), round(cue["end"] * 1000))
        for cue in group
    )


def _trim_stale_ass_cue_ends(
    first: Sequence[dict], second: Sequence[dict]
) -> tuple[list[dict], list[dict]]:
    cues = [*first, *second]
    latest_word_starts: dict[tuple[int, int], float] = {}
    for cue in cues:
        timings = cue.get("word_timings", [])
        if len(timings) < 2:
            continue
        last_word_start = max(word["start"] for word in timings)
        if cue["end"] - last_word_start > 3:
            interval = (round(cue["start"] * 1000), round(cue["end"] * 1000))
            latest_word_starts[interval] = max(
                latest_word_starts.get(interval, last_word_start),
                last_word_start,
            )

    def trim(track: Sequence[dict]) -> list[dict]:
        result = []
        for cue in track:
            interval = (round(cue["start"] * 1000), round(cue["end"] * 1000))
            last_word_start = latest_word_starts.get(interval)
            if last_word_start is None:
                result.append(cue)
                continue
            end = min(cue["end"], last_word_start + 0.5)
            result.append({**cue, "end": end} if end > cue["start"] else cue)
        return result

    return trim(first), trim(second)


def _serialize_same_end_cues(
    cues: Sequence[dict],
    *,
    paired_group_signatures: set[tuple[tuple[int, int], ...]] | None = None,
) -> list[dict]:
    ordered = sorted(cues, key=lambda cue: (cue["start"], cue["end"]))
    rolling_groups = _rolling_cue_groups(ordered)
    paired_group_signatures = paired_group_signatures or set()

    grouped_ids = {id(cue) for group in rolling_groups for cue in group}
    consumed: set[int] = set()
    serialized = []
    for group in rolling_groups:
        if _cue_group_signature(group) in paired_group_signatures:
            separator = (
                ""
                if CJK.search(
                    html.unescape(
                        re.sub(
                            r"<[^>]*>",
                            "",
                            _single_caption_line(group[0]["text"]),
                        )
                    )
                )
                else " "
            )
            contributions = []
            accumulated = ""
            for cue in group:
                current = html.unescape(
                    re.sub(r"<[^>]*>", "", _single_caption_line(cue["text"]))
                ).strip()
                if accumulated and current.casefold().startswith(accumulated.casefold()):
                    combined = current
                    contribution = current[len(accumulated):].lstrip()
                else:
                    combined = _append_rolling_text(
                        accumulated,
                        current,
                        separator=separator,
                    )
                    contribution = (
                        combined[len(accumulated):].lstrip()
                        if accumulated and combined.startswith(accumulated)
                        else combined
                    )
                contributions.append(contribution)
                accumulated = combined

            first_start_ms = round(group[0]["start"] * 1000)
            final_end_ms = max(round(cue["end"] * 1000) for cue in group)
            boundaries_ms = [
                round(cue["start"] * 1000) for cue in group[1:]
            ] + [final_end_ms]
            predecessors = [
                cue
                for cue in ordered
                if id(cue) not in grouped_ids
                and id(cue) not in consumed
                and round(cue["end"] * 1000) in boundaries_ms[:-1]
                and round(cue["start"] * 1000) < first_start_ms
            ]
            prefix = _combine_caption_entries(
                [
                    (
                        cue,
                        html.unescape(
                            re.sub(
                                r"<[^>]*>",
                                "",
                                _single_caption_line(cue["text"]),
                            )
                        ),
                    )
                    for cue in sorted(predecessors, key=lambda cue: cue["start"])
                ],
                separator=separator,
            )
            if predecessors:
                consumed.update(id(cue) for cue in predecessors)

            segment_start_ms = min(
                [first_start_ms]
                + [round(cue["start"] * 1000) for cue in predecessors]
            )
            group_segments = []
            for index, (cue, boundary_ms) in enumerate(zip(group, boundaries_ms)):
                text = contributions[index]
                if index == 0 and prefix:
                    text = _append_rolling_text(prefix, text, separator=separator)
                segment_end_ms = boundary_ms
                if text and segment_end_ms > segment_start_ms:
                    segment_start = segment_start_ms / 1000
                    segment_end = segment_end_ms / 1000
                    group_segments.append({
                        **group[0],
                        "start": segment_start,
                        "end": segment_end,
                        "text": text,
                        "word_timings": _word_timings_for_text(text, group),
                    })
                segment_start_ms = boundary_ms

            segment_index = 0
            while segment_index < len(group_segments):
                segment = group_segments[segment_index]
                if segment["end"] - segment["start"] >= 0.25:
                    segment_index += 1
                    continue
                if segment_index + 1 < len(group_segments):
                    following = group_segments[segment_index + 1]
                    following["text"] = (
                        segment["text"]
                        + (separator if separator else "")
                        + following["text"]
                    ).strip()
                    following["start"] = segment["start"]
                    del group_segments[segment_index]
                elif segment_index > 0:
                    previous = group_segments[segment_index - 1]
                    previous["text"] = (
                        previous["text"]
                        + (separator if separator else "")
                        + segment["text"]
                    ).strip()
                    previous["end"] = segment["end"]
                    del group_segments[segment_index]
                    segment_index -= 1
                else:
                    segment_index += 1
            serialized.extend(group_segments)
            consumed.update(id(cue) for cue in group)
            continue

        first_start_ms = round(group[0]["start"] * 1000)
        final_end_ms = max(round(cue["end"] * 1000) for cue in group)
        duration_ms = final_end_ms - first_start_ms
        if duration_ms <= 0:
            continue

        sample = _single_caption_line(group[0]["text"])
        separator = "" if CJK.search(html.unescape(re.sub(r"<[^>]*>", "", sample))) else " "
        full_text = ""
        for cue in group:
            current_text = html.unescape(
                re.sub(r"<[^>]*>", "", _single_caption_line(cue["text"]))
            )
            remainder = _rolling_delta(
                full_text,
                current_text,
                allow_single_word=True,
            )
            if full_text and remainder != current_text:
                full_text += (separator if separator and remainder else "") + remainder
            else:
                full_text = _append_rolling_text(
                    full_text,
                    current_text,
                    separator=separator,
                )
        words = _rolling_word_spans(full_text)
        if not words:
            continue

        split_points_ms = [
            round(cue["start"] * 1000)
            for cue in group[1:]
            if first_start_ms < round(cue["start"] * 1000) < final_end_ms
        ]
        split_points_ms = sorted(set(split_points_ms))
        segment_boundaries_ms = split_points_ms + [final_end_ms]
        cuts = []
        for boundary_ms in split_points_ms:
            timed_cut = _vtt_word_cut(
                full_text,
                group,
                boundary_ms / 1000,
            )
            if timed_cut is None:
                elapsed_ms = boundary_ms - first_start_ms
                timed_cut = max(
                    0,
                    min(
                        len(words),
                        (2 * elapsed_ms * len(words) + duration_ms)
                        // (2 * duration_ms),
                    ),
                )
            cuts.append(timed_cut)
        cuts.append(len(words))

        preceding_at_boundary: dict[int, list[dict]] = {}
        for boundary_ms in split_points_ms:
            predecessors = [
                cue
                for cue in ordered
                if id(cue) not in grouped_ids
                and id(cue) not in consumed
                and round(cue["end"] * 1000) == boundary_ms
                and round(cue["start"] * 1000) < first_start_ms
            ]
            if predecessors:
                preceding_at_boundary[boundary_ms] = predecessors
                consumed.update(id(cue) for cue in predecessors)

        previous_cut = 0
        segment_start_ms = first_start_ms
        for boundary_ms, cut in zip(segment_boundaries_ms, cuts):
            cut = max(previous_cut, min(cut, len(words)))
            start_offset = (
                0
                if previous_cut == 0
                else words[previous_cut].start()
                if previous_cut < len(words)
                else len(full_text)
            )
            end_offset = (
                len(full_text)
                if cut == len(words)
                else words[cut].start() if cut > previous_cut else start_offset
            )
            segment_text = full_text[start_offset:end_offset].strip()
            predecessors = preceding_at_boundary.get(boundary_ms, [])
            if predecessors:
                prefix = _combine_caption_entries(
                    [
                        (
                            cue,
                            html.unescape(
                                re.sub(
                                    r"<[^>]*>",
                                    "",
                                    _single_caption_line(cue["text"]),
                                )
                            ),
                        )
                        for cue in sorted(predecessors, key=lambda cue: cue["start"])
                    ],
                    separator=separator,
                )
                segment_text = _append_rolling_text(
                    prefix, segment_text, separator=separator
                )
                if previous_cut == 0:
                    segment_start_ms = min(
                        segment_start_ms,
                        *(round(cue["start"] * 1000) for cue in predecessors),
                    )
            segment_end_ms = boundary_ms
            if segment_text and segment_end_ms > segment_start_ms:
                segment_start = segment_start_ms / 1000
                segment_end = segment_end_ms / 1000
                serialized.append({
                    **group[0],
                    "start": segment_start,
                    "end": segment_end,
                    "text": segment_text,
                    "word_timings": _word_timings_for_text(segment_text, group),
                })
            previous_cut = cut
            segment_start_ms = boundary_ms
        consumed.update(id(cue) for cue in group)

    serialized.extend(cue for cue in ordered if id(cue) not in consumed)
    return sorted(serialized, key=lambda cue: (cue["start"], cue["end"]))


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
    box_style: str = "glass-diamond",
) -> str:
    if not first or not second:
        raise ValueError("Both subtitle tracks must contain cues.")
    first, second = _trim_stale_ass_cue_ends(first, second)
    for name, font in (("Chinese", first_font), ("English", second_font)):
        if not font.strip() or any(char in font for char in ",\r\n{}"):
            raise ValueError(f"{name} font name must be non-empty and cannot contain commas or braces.")
    if second_font_size < 1 or not 0.5 <= first_font_scale <= 3:
        raise ValueError("Font size must be positive and Chinese font scale must be between 0.5 and 3.")
    if caption_order not in {"chinese-first", "english-first"}:
        raise ValueError("Caption order must be 'chinese-first' or 'english-first'.")
    if box_style not in {*BOX_STYLES, "none"}:
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
    highlight_ass_color = ass_color(ENGLISH_HIGHLIGHT_COLOR)
    shadow_ass_color = ass_color(shadow_color, opacity=35)
    box_ass_color = ass_color(box_color, opacity=round(box_opacity * 100))
    box_edge_color = ass_color(BOX_STYLES.get(box_style, BOX_STYLES["glass-diamond"])["edge"], opacity=24)
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
    styles = [
        f"Style: Chinese,{first_font},{first_font_size:g},{first_ass_color},&H000000FF,"
        f"&H80000000,{shadow_ass_color},-1,0,0,0,100,100,0,0,1,2,1,5,0,0,0,1",
        f"Style: English,{second_font},{second_font_size},{highlight_ass_color},{second_ass_color},"
        f"&H80000000,{shadow_ass_color},0,0,0,0,100,100,0,0,1,2,1,5,0,0,0,1",
    ]
    if box_style != "none":
        styles += [
            f"Style: CaptionBox,Arial,1,{box_ass_color},&H000000FF,"
            f"&H00000000,{box_edge_color},0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1",
            f"Style: CaptionFacet,Arial,1,{facet_color},&H000000FF,"
            f"&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        ]
    first_group_signatures = {
        _cue_group_signature(group) for group in _rolling_cue_groups(first)
    }
    second_group_signatures = {
        _cue_group_signature(group) for group in _rolling_cue_groups(second)
    }
    paired_group_signatures = first_group_signatures & second_group_signatures
    first = _serialize_same_end_cues(
        first,
        paired_group_signatures=paired_group_signatures,
    )
    second = _serialize_same_end_cues(
        second,
        paired_group_signatures=paired_group_signatures,
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

    if box_style != "none":
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
        cn_size = fit_font_size(chinese, first_font_size, available_width) if chinese else 0
        en_size = fit_font_size(english, second_font_size, available_width) if english else 0
        order = (
            (("english", english, en_size), ("chinese", chinese, cn_size))
            if caption_order == "english-first"
            else (("chinese", chinese, cn_size), ("english", english, en_size))
        )
        visible_lines = [line for line in order if line[1]]
        style = "English" if visible_lines[0][0] == "english" else "Chinese"
        event_start = start
        english_word_timings = _word_timings_for_text(english, active)
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
            body += f"\\fs{size:g}" + "}"
            body += (
                ass_karaoke_text(
                    text,
                    end - event_start,
                    start=event_start,
                    fallback_start=start,
                    word_timings=english_word_timings,
                )
                if language == "english"
                else ass_escape_text(text)
            )
        body = f"{{\\an5\\pos({area_center_x:g},{area_center_y:g})}}" + body
        event = {
            "start": event_start,
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


def srt_to_vtt(content: str) -> str:
    validate_srt(content)
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(
        r"(?m)^(\d{2}:\d{2}:\d{2}),(\d{3}) --> "
        r"(\d{2}:\d{2}:\d{2}),(\d{3})$",
        r"\1.\2 --> \3.\4",
        normalized,
    )
    return "WEBVTT\n\n" + normalized.rstrip("\n") + "\n"


def write_vtt(path: Path, content: str) -> None:
    converted = srt_to_vtt(content)
    cue_count = len(parse_vtt(converted))
    path.write_text(converted, encoding="utf-8", newline="")
    written = path.read_text(encoding="utf-8")
    if len(parse_vtt(written)) != cue_count:
        raise OSError(f"WebVTT output failed validation after writing: {path}")


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


def track_formats(tracks: dict, language: str) -> list[str]:
    """List the caption formats YouTube publishes for one language."""
    entries = tracks.get(language) or []
    formats = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("ext"):
            formats.append(str(entry["ext"]))
    return formats


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


def load_public_proxy_selector():
    """Load the public proxy selector module from the repository scripts directory."""
    selector_path = Path(__file__).resolve().parent / "select_public_ytdlp_proxy.py"
    spec = importlib.util.spec_from_file_location("select_public_ytdlp_proxy", selector_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select_fastest_public_proxy() -> str | None:
    """Iterate every public proxy and return the fastest one that works.

    Used only as a fallback when the direct connection cannot complete a YouTube
    download, so the workflow can retry through the fastest known public proxy
    instead of failing on the first blocked direct egress.
    """
    try:
        selector = load_public_proxy_selector()
        candidates = selector.load_pool()
        if not candidates:
            candidates = [
                proxy for proxies in selector.fetch_lists().values() for proxy in proxies
            ]
        selected = selector.select_fastest_working(candidates, selector.probe_proxy)
    except Exception:  # noqa: BLE001 - the fallback must never mask the real error
        return None
    if selected is None:
        return None
    return selected[0]


def ytdlp_failure(error: subprocess.CalledProcessError, proxy: str | None) -> RuntimeError:
    details = (error.stderr or error.stdout or str(error)).strip()
    if proxy:
        details = details.replace(proxy, "[configured proxy]")
    message = f"yt-dlp failed: {details}" if details else f"yt-dlp exited with code {error.returncode}."
    return RuntimeError(message)


def run_ytdlp(
    args: Sequence[str], *, show_progress: bool = False
) -> subprocess.CompletedProcess:
    executable = shutil.which("yt-dlp")
    if not executable:
        raise RuntimeError("yt-dlp is not installed or is not available on PATH.")
    javascript_runtime = ["--js-runtimes", "deno"] if shutil.which("deno") else []
    cookies_file = os.environ.get("YTDLP_COOKIES_FILE")
    cookies_arguments = []
    if cookies_file:
        if not Path(cookies_file).is_file():
            raise RuntimeError("The configured yt-dlp cookies file does not exist.")
        cookies_arguments = ["--cookies", cookies_file]
    proxy = next(
        (
            os.environ[name]
            for name in (
                "YTDLP_PROXY",
                "HTTPS_PROXY",
                "https_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "ALL_PROXY",
                "all_proxy",
            )
            if os.environ.get(name)
        ),
        None,
    )

    def build(proxy_value: str | None) -> list[str]:
        proxy_arguments = ["--proxy", proxy_value] if proxy_value else []
        return [executable, *javascript_runtime, *cookies_arguments, *proxy_arguments, *args]

    def execute(proxy_value: str | None) -> subprocess.CompletedProcess:
        return subprocess.run(
            build(proxy_value),
            check=True,
            capture_output=not show_progress,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    try:
        return execute(proxy)
    except subprocess.CalledProcessError as error:
        if proxy:
            raise ytdlp_failure(error, proxy) from error
        fallback_proxy = select_fastest_public_proxy()
        if not fallback_proxy:
            raise ytdlp_failure(error, proxy) from error
        try:
            return execute(fallback_proxy)
        except subprocess.CalledProcessError as retry_error:
            raise ytdlp_failure(retry_error, fallback_proxy) from retry_error


def download_track(
    url: str,
    video_id: str,
    language: str,
    track_kind: str,
    directory: Path,
    wait: int,
    formats: Sequence[str],
) -> tuple[list[Path], list[dict], Path]:
    """Download one caption track in every published format.

    All seven formats are kept byte-for-byte as yt-dlp writes them. The cues
    used for the merge and for the ASS are taken from the format that carries
    the most precise timing data.
    """
    requested = [ext for ext in CAPTION_FORMATS if ext in set(formats)]
    if not requested:
        raise RuntimeError(f"YouTube publishes no supported caption format for {language}.")
    print(
        f"  Requesting {language} ({track_kind}) as {', '.join(requested)}; "
        f"yt-dlp will wait {wait}s before each request.",
        flush=True,
    )
    for extension in requested:
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
            extension,
            "-o",
            str(directory / "%(id)s.%(language)s.%(ext)s"),
            url,
        ])
        run_ytdlp(args, show_progress=True)
    matches = sorted(
        path
        for path in directory.glob(f"{video_id}.*")
        if path.is_file()
        and path.name.lower().endswith(tuple(f".{language.lower()}.{ext}" for ext in CAPTION_FORMATS))
    )
    if not matches:
        raise RuntimeError(f"yt-dlp did not produce the requested {language} caption files.")
    cues, source_path = select_most_precise_cues(matches)
    return matches, cues, source_path


def load_track(
    url: str,
    video_id: str,
    language: str,
    track_kind: str,
    directory: Path,
    wait: int,
    formats: Sequence[str],
    local_file: Path | None = None,
) -> tuple[list[Path], list[dict], Path]:
    """Use a local caption file when given, otherwise download every format."""
    if local_file is not None:
        if not local_file.is_file():
            raise ValueError(f"Local caption file not found: {local_file}")
        print(f"  Using the local caption file {local_file} for {language}.", flush=True)
        cues = parse_caption_file(local_file)
        return [local_file], cues, local_file
    return download_track(url, video_id, language, track_kind, directory, wait, formats)


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
        choices=("always",),
        default="always",
        help="Keep the background box visible throughout the video.",
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
        "--source-video",
        help="Existing video file to burn the subtitles into instead of downloading it again.",
    )
    parser.add_argument(
        "--local-file",
        action="append",
        default=[],
        metavar="LANGUAGE=PATH",
        help="Use a local caption file for a language instead of downloading it, e.g. zh-Hans=translation.srt.",
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
        video_duration = metadata.get("duration")
        if (
            not isinstance(video_duration, (int, float))
            or not math.isfinite(video_duration)
            or video_duration <= 0
        ):
            raise ValueError(
                "Video duration is unavailable; cannot guarantee the background box "
                "remains visible through the full video."
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
        local_tracks = {}
        for entry in args.local_file:
            language, separator, path = entry.partition("=")
            if not separator or not language.strip() or not path.strip():
                raise ValueError("--local-file expects LANGUAGE=PATH")
            local_tracks[language.strip().lower()] = Path(path.strip())
        second_files, second, second_source = load_track(
            args.url,
            video_id,
            second_code,
            second_kind,
            folder,
            subtitle_wait(second_code, args.sleep_subtitles),
            track_formats(tracks, second_code),
            local_tracks.get(second_code.lower()),
        )
        print(f"Step 3/5: Downloading top track ({first_code})...", flush=True)
        first_files, first, first_source = load_track(
            args.url,
            video_id,
            first_code,
            first_kind,
            folder,
            subtitle_wait(first_code, args.sleep_subtitles),
            track_formats(tracks, first_code),
            local_tracks.get(first_code.lower()),
        )
        print(
            f"  Formats kept for {first_code}: {', '.join(sorted(path.suffix.lstrip('.') for path in first_files))}",
            flush=True,
        )
        print(
            f"  Formats kept for {second_code}: {', '.join(sorted(path.suffix.lstrip('.') for path in second_files))}",
            flush=True,
        )
        print(
            f"  Most precise sources: {first_source.name} + {second_source.name}",
            flush=True,
        )

        print("Step 4/5: Merging the unchanged downloaded tracks...", flush=True)
        combined_kind = f"{first_kind}+{second_kind}"
        output_stem = f"{combined_kind}_Bilingual_{first_code}-{second_code}"
        merged_srt = align_and_merge(first, second)
        output = folder / f"{output_stem}.srt"
        write_srt(output, merged_srt)
        vtt_output = folder / f"{output_stem}.vtt"
        write_vtt(vtt_output, merged_srt)
        ass_output = folder / f"{output_stem}.ass"
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
                video_duration=video_duration,
                box_style=args.box_style,
            ),
        )
        print("Step 5/6: Validating generated subtitle files...", flush=True)
        cue_count = validate_srt(output.read_text(encoding="utf-8-sig"))
        print(f"  OK: {output.name} ({cue_count} cues)", flush=True)
        print(f"  OK: {vtt_output.name} (WebVTT)", flush=True)
        print(f"  OK: {ass_output.name} (ASS styles: Chinese above English)", flush=True)
        if args.burn_in:
            if args.source_video:
                source_video = Path(args.source_video)
                if not source_video.is_file():
                    raise ValueError(f"--source-video file not found: {source_video}")
                print(f"Step 6/6: Burning in the ASS subtitles into {source_video.name}...", flush=True)
            else:
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
        for path in [*first_files, *second_files]:
            print(path.resolve())
        print("Done. Subtitle files:", flush=True)
        print(output.resolve())
        print(vtt_output.resolve())
        print(ass_output.resolve())
        if args.burn_in:
            print(burned_output.resolve())
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
