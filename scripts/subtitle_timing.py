#!/usr/bin/env python3
"""Module 2a - subtitle timing precision office.

YouTube publishes the same transcript in seven caption formats. This module
fuses them into one timeline and turns it into a styled ASS file:

``json3`` > ``srv3`` > ``srv2`` > ``srv1`` > ``vtt`` > ``ttml`` > ``srt``

the order of both timing precision and trust. ``json3`` supplies per-word
``tOffsetMs`` offsets; ``srv3``/``srv2`` repeat them as XML attributes;
``srv1``/``ttml``/``vtt``/``srt`` contribute cue boundaries. Every format that
describes a cue is matched and every per-word start is combined with a
weighted median, so one damaged or rounded track cannot shift the result.

The internal timeline is **integer nanoseconds**. YouTube's source resolution
is 1 ms, so the fused values are exact multiples of that resolution rather than
invented precision; the nanosecond type keeps sums and medians lossless.
The generated ASS and precision VTT are clipped at the video duration, while
the original downloaded caption files remain byte-for-byte unchanged.

ASS rendering has a hard limit of **centiseconds**: libass accepts exactly two
fractional digits, and a timestamp such as ``0:00:01.234`` makes the whole
event disappear (verified with FFmpeg/libass at 1000 fps). The ASS therefore
quantizes the nanosecond timeline to 10 ms, while ``TIMING.json`` keeps the
fused nanosecond values and their provenance. The maximum quantization error
is 5 ms and is reported with the output.
"""

from __future__ import annotations

import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import download_youtube_bilingual_srt as captions  # noqa: E402

NS_PER_SECOND = 1_000_000_000
CS_PER_SECOND = 100
ASS_MAX_ERROR_NS = 5_000_000
FORMAT_PRIORITY = ("json3", "srv3", "srv2", "srv1", "vtt", "ttml", "srt")
# json3 is YouTube's primary timing source; srv3/srv2 carry the same offsets in
# XML. Lower weights still let older formats break ties and fill gaps.
FORMAT_WEIGHTS = {
    "json3": 6.0,
    "srv3": 5.0,
    "srv2": 4.0,
    "srv1": 3.0,
    "vtt": 2.0,
    "ttml": 1.5,
    "srt": 1.0,
}
FORMAT_RESOLUTION = {
    "json3": "1 ms",
    "srv3": "1 ms",
    "srv2": "1 ms",
    "srv1": "1 ms",
    "vtt": "1 ms",
    "ttml": "1 ms",
    "srt": "10 ms",
}
LEGACY_NAMES = {
    "en-orig": {"srt": ("auto-generated_English.srt",), "vtt": ("source_English.vtt",)},
    "zh-Hans": {"srt": ("auto-translated_Chinese.srt",)},
}
LANGUAGE_LABELS = {
    "en-orig": "English",
    "en": "English",
    "zh-Hans": "Chinese",
    "zh": "Chinese",
}


def language_label(code: str) -> str:
    return LANGUAGE_LABELS.get(code, code)


def to_ns(seconds: float | int | str) -> int:
    """Convert a caption timestamp to integer nanoseconds, half-up."""
    return int(round(float(seconds) * NS_PER_SECOND))


def to_seconds(nanoseconds: int) -> float:
    return nanoseconds / NS_PER_SECOND


def quantize_centiseconds(nanoseconds: int) -> int:
    """Round a nanosecond value to the nearest ASS centisecond, half-up."""
    return (nanoseconds + ASS_MAX_ERROR_NS) // (2 * ASS_MAX_ERROR_NS)


def quantization_error_ms(nanoseconds: int) -> float:
    return abs(quantize_centiseconds(nanoseconds) * 10_000_000 - nanoseconds) / 1_000_000


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_text(value: str) -> str:
    """Decode the double-escaped entities some srv1 files contain."""
    text = html.unescape(html.unescape(value))
    text = text.replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def discover_tracks(folder: Path, video_id: str, language: str) -> dict[str, Path]:
    """Find every published caption format for one language in an archive."""
    folder = Path(folder)
    found: dict[str, Path] = {}
    for extension in FORMAT_PRIORITY:
        patterns = [
            f"{video_id}.{language}.{extension}",
            f"{video_id}.*.{language}.{extension}",
        ]
        for pattern in patterns:
            matches = sorted(folder.glob(pattern))
            if matches:
                found[extension] = matches[0]
                break
    for extension, names in LEGACY_NAMES.get(language, {}).items():
        if extension in found:
            continue
        for name in names:
            candidate = folder / name
            if candidate.is_file():
                found[extension] = candidate
                break
    return {extension: found[extension] for extension in FORMAT_PRIORITY if extension in found}


def parse_formats(files: dict[str, Path]) -> dict[str, list[dict]]:
    """Parse each format, dropping rolling WebVTT duplicates."""
    parsed: dict[str, list[dict]] = {}
    for extension, path in files.items():
        try:
            cues = captions.parse_caption_file(path)
        except (OSError, ValueError):
            continue
        if extension == "vtt":
            cues = captions._without_rolling_cues(cues)
        if cues:
            parsed[extension] = cues
    return parsed


def weighted_median(values: Sequence[tuple[int, float]]) -> int:
    """Median of integer values with per-source weights."""
    pairs = sorted((int(value), float(weight)) for value, weight in values if weight > 0)
    if not pairs:
        raise ValueError("Cannot take a median of no values.")
    total = sum(weight for _, weight in pairs)
    accumulated = 0.0
    for value, weight in pairs:
        accumulated += weight
        if accumulated >= total / 2:
            return max(0, value)
    return max(0, pairs[-1][0])


def _cue_overlap(left: dict, right: dict) -> int:
    return max(
        0,
        min(to_ns(left["end"]), to_ns(right["end"]))
        - max(to_ns(left["start"]), to_ns(right["start"])),
    )


def _text_similarity(left: str, right: str) -> float:
    left, right = normalize_text(left), normalize_text(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def match_cues(primary: Sequence[dict], other: Sequence[dict]) -> list[dict | None]:
    """Match every primary cue to the best overlapping cue of another format."""
    ordered = sorted(other, key=lambda cue: to_ns(cue["start"]))
    matches: list[dict | None] = []
    cursor = 0
    for cue in primary:
        cue_start, cue_end = to_ns(cue["start"]), to_ns(cue["end"])
        while cursor < len(ordered) and to_ns(ordered[cursor]["end"]) <= cue_start:
            cursor += 1
        best = None
        best_score = 0.0
        index = cursor
        while index < len(ordered) and to_ns(ordered[index]["start"]) < cue_end:
            candidate = ordered[index]
            overlap = _cue_overlap(cue, candidate)
            if overlap:
                shorter = max(
                    1,
                    min(cue_end - cue_start, to_ns(candidate["end"]) - to_ns(candidate["start"])),
                )
                similarity = _text_similarity(cue["text"], candidate["text"])
                score = overlap / shorter + similarity
                if score > best_score:
                    best, best_score = candidate, score
            index += 1
        matches.append(best)
    return matches


def fuse_values(pairs: Sequence[tuple[str, int]]) -> int:
    """Combine one timestamp reported by several formats.

    Exact agreement keeps the highest-priority source; a real disagreement is
    resolved with the weighted median so one outlier cannot win.
    """
    if not pairs:
        raise ValueError("No timestamps to fuse.")
    ordered = sorted(pairs, key=lambda item: (item[1], FORMAT_PRIORITY.index(item[0])))
    if len(ordered) == 1 or ordered[-1][1] - ordered[0][1] <= 1_000_000:
        return min(ordered, key=lambda item: FORMAT_PRIORITY.index(item[0]))[1]
    return weighted_median(
        [(value, FORMAT_WEIGHTS[extension]) for extension, value in ordered]
    )


def _fuse_words(
    group: dict[str, dict],
    cue_start_ns: int,
    cue_end_ns: int,
    cue_index: int,
    conflicts: list[dict],
) -> list[dict]:
    base_extension = next(
        (
            extension
            for extension in FORMAT_PRIORITY
            if extension in group and group[extension].get("word_timings")
        ),
        None,
    )
    if base_extension is None:
        return []
    base_words = group[base_extension]["word_timings"]
    usable = {
        extension: cue["word_timings"]
        for extension, cue in group.items()
        if cue.get("word_timings") and len(cue["word_timings"]) == len(base_words)
    }
    fused: list[dict] = []
    for index, word in enumerate(base_words):
        pairs = [
            (extension, to_ns(words[index]["start"]))
            for extension, words in usable.items()
        ]
        # json3 is YouTube's primary per-word timing. The XML formats often
        # group several words into one segment and spread them evenly, so they
        # corroborate or fill gaps but must not override a json3 offset.
        if "json3" in usable:
            start_ns = min(
                cue_end_ns,
                max(cue_start_ns, to_ns(usable["json3"][index]["start"])),
            )
        else:
            start_ns = min(
                cue_end_ns,
                max(cue_start_ns, fuse_values(pairs)),
            )
        spread = max(value for _, value in pairs) - min(value for _, value in pairs)
        if spread > 50_000_000 and len(conflicts) < 50:
            conflicts.append(
                {
                    "cue": cue_index,
                    "word": index,
                    "text": normalize_text(str(word.get("text", ""))),
                    "spread_ms": round(spread / 1_000_000, 3),
                    "formats": sorted(extension for extension, _ in pairs),
                }
            )
        fused.append(
            {
                "text": normalize_text(str(word.get("text", ""))),
                "start_ns": start_ns,
                "end_ns": cue_end_ns,
                "sources": sorted(extension for extension, _ in pairs),
            }
        )
    for index in range(1, len(fused)):
        fused[index]["start_ns"] = max(
            fused[index]["start_ns"], fused[index - 1]["start_ns"]
        )
        fused[index - 1]["end_ns"] = fused[index]["start_ns"]
    if fused:
        fused[-1]["end_ns"] = max(fused[-1]["start_ns"], cue_end_ns)
    return fused


def fuse_track(
    language: str,
    parsed: dict[str, list[dict]],
    source_files: dict[str, Path] | None = None,
) -> dict:
    """Fuse every parsed format of one language into a nanosecond track."""
    available = [extension for extension in FORMAT_PRIORITY if extension in parsed]
    if not available:
        raise ValueError(f"No usable caption format for {language}.")
    primary_extension = available[0]
    primary = parsed[primary_extension]
    matches = {
        extension: match_cues(primary, parsed[extension])
        for extension in available[1:]
    }
    conflicts: list[dict] = []
    cues: list[dict] = []
    for index, cue in enumerate(primary):
        group = {primary_extension: cue}
        for extension in available[1:]:
            match = matches[extension][index]
            if match is not None:
                group[extension] = match
        start_pairs = [(extension, to_ns(item["start"])) for extension, item in group.items()]
        end_pairs = [(extension, to_ns(item["end"])) for extension, item in group.items()]
        start_ns = fuse_values(start_pairs)
        end_ns = fuse_values(end_pairs)
        text = normalize_text(str(cue.get("text", "")))
        if not text or end_ns <= start_ns:
            continue
        words = _fuse_words(group, start_ns, end_ns, index, conflicts)
        if words:
            start_ns = min(start_ns, words[0]["start_ns"])
            end_ns = max(end_ns, words[-1]["start_ns"])
        cues.append(
            {
                "start_ns": start_ns,
                "end_ns": end_ns,
                "text": text,
                "word_timings": words,
                "sources": [extension for extension in FORMAT_PRIORITY if extension in group],
            }
        )
    if not cues:
        raise ValueError(f"No usable cues after fusing {language}.")
    return {
        "language": language,
        "timebase": "nanosecond",
        "primary_format": primary_extension,
        "formats_used": available,
        "source_resolution": {extension: FORMAT_RESOLUTION[extension] for extension in available},
        "source_files": {extension: str(path) for extension, path in (source_files or {}).items()},
        "cue_count": len(cues),
        "cues": cues,
        "conflicts": conflicts,
    }


def clip_track_to_duration(track: dict, duration_seconds: float) -> dict:
    """Copy a fused track with all cue and word timings bounded to the video."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Video duration must be a finite, positive number.")
    duration_ns = to_ns(duration_seconds)
    source_cues = track["cues"]
    clipped_cues = []
    clipped_count = 0
    discarded_count = 0
    source_last_end_ns = max((cue["end_ns"] for cue in source_cues), default=0)
    for cue in source_cues:
        start_ns = max(0, int(cue["start_ns"]))
        end_ns = min(duration_ns, int(cue["end_ns"]))
        changed = start_ns != cue["start_ns"] or end_ns != cue["end_ns"]
        if changed:
            clipped_count += 1
        if start_ns >= duration_ns or end_ns <= start_ns:
            discarded_count += 1
            continue
        clipped_cue = {**cue, "start_ns": start_ns, "end_ns": end_ns}
        clipped_words = []
        for word in cue.get("word_timings") or []:
            word_start_ns = max(start_ns, int(word["start_ns"]))
            word_end_ns = min(end_ns, int(word["end_ns"]))
            if word_end_ns > word_start_ns:
                clipped_words.append(
                    {
                        **word,
                        "start_ns": word_start_ns,
                        "end_ns": word_end_ns,
                    }
                )
        clipped_cue["word_timings"] = clipped_words
        clipped_cues.append(clipped_cue)
    if not clipped_cues:
        raise ValueError(
            f"No {track.get('language', 'subtitle')} cues fall within the video duration."
        )
    return {
        **track,
        "cue_count": len(clipped_cues),
        "source_cue_count": len(source_cues),
        "clipped_cue_count": clipped_count,
        "discarded_cue_count": discarded_count,
        "source_last_cue_end_ns": source_last_end_ns,
        "video_duration_ns": duration_ns,
        "cues": clipped_cues,
    }


def build_tracks(
    caption_dir: Path,
    video_id: str,
    languages: Sequence[str],
) -> dict[str, dict]:
    """Discover, parse, and fuse every requested language in an archive folder."""
    tracks: dict[str, dict] = {}
    for language in languages:
        files = discover_tracks(caption_dir, video_id, language)
        if not files:
            raise ValueError(
                f"No caption formats found for {language} in {caption_dir}"
            )
        parsed = parse_formats(files)
        if not parsed:
            raise ValueError(
                f"The caption files for {language} could not be parsed: "
                + ", ".join(path.name for path in files.values())
            )
        tracks[language] = fuse_track(language, parsed, files)
    return tracks


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


def to_ass_cues(track: dict) -> list[dict]:
    """Convert a nanosecond track into the float cue model used by build_ass."""
    cues: list[dict] = []
    for cue in track["cues"]:
        item = {
            "start": to_seconds(cue["start_ns"]),
            "end": to_seconds(cue["end_ns"]),
            "text": cue["text"],
            "start_timestamp": captions.format_timestamp(to_seconds(cue["start_ns"])),
            "end_timestamp": captions.format_timestamp(to_seconds(cue["end_ns"])),
        }
        words = cue.get("word_timings") or []
        if words:
            item["word_timings"] = [
                {
                    "start": to_seconds(word["start_ns"]),
                    "end": to_seconds(word["end_ns"]),
                    "text": word["text"],
                }
                for word in words
            ]
        cues.append(item)
    return cues


def precision_stats(tracks: dict[str, dict]) -> dict:
    """Summarize the fused timeline and its loss when quantized to ASS."""
    values = []
    words = 0
    for track in tracks.values():
        for cue in track["cues"]:
            values.extend((cue["start_ns"], cue["end_ns"]))
            values.extend(word["start_ns"] for word in cue.get("word_timings") or [])
            words += len(cue.get("word_timings") or [])
    errors = [quantization_error_ms(value) for value in values]
    return {
        "timebase": "nanosecond",
        "ass_resolution": "centisecond",
        "ass_max_quantization_error_ms": 5.0,
        "measured_max_quantization_error_ms": round(max(errors), 6) if errors else 0.0,
        "measured_mean_quantization_error_ms": round(sum(errors) / len(errors), 6) if errors else 0.0,
        "timestamps": len(values),
        "word_timings": words,
        "source_resolution": sorted(
            {resolution for track in tracks.values() for resolution in track["source_resolution"].values()}
        ),
    }


def build_precise_ass(
    first_track: dict,
    second_track: dict,
    *,
    box_style: str = "none",
    box_opacity: float = 0.85,
    box_color: str | None = None,
    first_font: str = "Microsoft YaHei",
    second_font: str = "Arial",
    second_font_size: int = 48,
    first_font_scale: float = 1.2,
    caption_area: tuple[int, int, int, int] = captions.CAPTION_AREA,
    first_color: str = "#FFC928",
    second_color: str = "#FFFFFF",
    caption_order: str = "chinese-first",
    video_duration: float | None = None,
) -> tuple[str, dict]:
    """Build the bilingual ASS from two fused tracks.

    The track order follows ``build_ass``: the first track is the upper line
    (Chinese in the default Classic preset), the second is the lower line.
    """
    style = captions.BOX_STYLES.get(box_style)
    if box_color is None:
        box_color = style["color"] if style else "#0F1647"
    content = captions.build_ass(
        to_ass_cues(first_track),
        to_ass_cues(second_track),
        first_font=first_font,
        second_font=second_font,
        second_font_size=second_font_size,
        first_font_scale=first_font_scale,
        caption_area=caption_area,
        first_color=first_color,
        second_color=second_color,
        box_color=box_color,
        box_opacity=box_opacity,
        caption_order=caption_order,
        video_duration=video_duration,
        box_style=box_style,
    )
    timestamp = re.compile(
        r"^Dialogue: [^,]+,\d+:\d{2}:\d{2}\.(\d{2}),\d+:\d{2}:\d{2}\.(\d{2}),"
    )
    for line in content.splitlines():
        if line.startswith("Dialogue: ") and not timestamp.match(line):
            raise RuntimeError(
                "Generated ASS contains a timestamp that libass cannot parse; "
                "ASS timestamps must use exactly two fractional digits."
            )
    stats = precision_stats({first_track["language"]: first_track, second_track["language"]: second_track})
    header = [
        "; Module 2 precision timing (subtitle timing office)",
        f"; timebase={stats['timebase']} ass-resolution={stats['ass_resolution']}",
        f"; ass-max-quantization-error-ms={stats['ass_max_quantization_error_ms']}",
        "; sources="
        + ",".join(
            f"{language}:{'+'.join(track['formats_used'])}"
            for language, track in ((first_track["language"], first_track), (second_track["language"], second_track))
        ),
        f"; cues={sum(track['cue_count'] for track in (first_track, second_track))}",
    ]
    marker = "\n[V4+ Styles]"
    content = content.replace(marker, "\n" + "\n".join(header) + "\n" + marker, 1)
    return content, stats


def write_timing_report(
    path: Path,
    tracks: dict[str, dict],
    *,
    video_id: str,
    video: Path,
) -> dict:
    """Write the lossless nanosecond timeline next to the ASS file."""
    payload = {
        "generated_at": utc_now(),
        "module": "module2a-subtitle-timing",
        "timebase": "nanosecond",
        "ass_resolution": "centisecond",
        "ass_timestamp_limit": (
            "libass accepts exactly two fractional digits; three or more digits "
            "make the event disappear, so the ASS is quantized to 10 ms"
        ),
        "ass_max_quantization_error_ms": 5.0,
        "video_id": video_id,
        "video": str(video),
        "statistics": precision_stats(tracks),
        "tracks": tracks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def format_vtt_timestamp(nanoseconds: int) -> str:
    """Format a nanosecond value as a millisecond WebVTT timestamp."""
    milliseconds = (max(0, nanoseconds) + 500_000) // 1_000_000
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def write_precision_vtt(track: dict, path: Path) -> dict:
    """Write the fused track as a 1 ms WebVTT sidecar.

    ASS is limited to centiseconds, so this file is the most precise subtitle
    document a normal player can consume; ``TIMING.json`` keeps the nanoseconds.
    """
    lines = ["WEBVTT", ""]
    written = 0
    for cue in track["cues"]:
        text = cue["text"].strip()
        if not text:
            continue
        start = format_vtt_timestamp(cue["start_ns"])
        end_ns = max(cue["end_ns"], cue["start_ns"] + 1_000_000)
        end = format_vtt_timestamp(end_ns)
        lines.extend([f"{start} --> {end}", text, ""])
        written += 1
    content = "\n".join(lines).rstrip("\n") + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if written == 0:
        raise ValueError(f"Precision WebVTT for {track['language']} has no cues.")
    return {
        "path": str(path),
        "language": track["language"],
        "format": "WebVTT",
        "resolution": "1 ms",
        "cues": written,
        "size_bytes": path.stat().st_size,
    }
