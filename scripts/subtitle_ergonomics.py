#!/usr/bin/env python3
"""Module 2c - subtitle ergonomics office.

The rules follow published broadcast/streaming guidance rather than taste:

* Netflix English timed text: 20 CPS adult, 17 CPS children, 42 characters
  per line, two lines, minimum 20 frames, maximum 7 seconds, 2-frame gap.
* Netflix Simplified Chinese: 9 CPS adult, 7 CPS children, 16 characters per
  line for Originals (23 for automated QC), two lines.
* BBC/Ofcom: 160-180 words per minute is the guideline ceiling; above 200 WPM
  is hard to follow.
* Netflix counts spaces and punctuation toward reading speed; for
  character-based languages, half-width characters count 0.5.

This module reports violations with cue timing, line text, measured value and
limit, so the burn step can warn or fail before rendering. It does not rewrite
subtitle text: shortening lines changes meaning and must stay an editorial
decision.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import download_youtube_bilingual_srt as captions  # noqa: E402
import subtitle_timing  # noqa: E402

CJK = re.compile(
    r"[\u2e80-\u2fff\u3000-\u30ff\u3100-\u312f\u3130-\u318f"
    r"\u31a0-\u31bf\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\ua960-\ua97f\uac00-\ud7ff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]"
)
ASS_TAG = re.compile(r"\{[^}]*\}")

# Netflix general requirements: 20 frames at 24 fps = 5/6 s; maximum 7 s.
DEFAULT_MIN_DURATION = 20 / 24
DEFAULT_MAX_DURATION = 7.0
DEFAULT_MIN_GAP_FRAMES = 2


@dataclass(frozen=True)
class ErgonomicsRules:
    """Limits for one language and audience."""

    language: str
    max_cps: float
    max_chars_line: int
    max_lines: int = 2
    min_duration: float = DEFAULT_MIN_DURATION
    max_duration: float = DEFAULT_MAX_DURATION
    min_gap: float = 0.08
    children: bool = False


def rules_for(language: str, *, fps: float = 24.0, children: bool = False) -> ErgonomicsRules:
    """Return the published limits for a language code."""
    code = (language or "en").lower()
    chinese = code.startswith("zh") or code in {"cn", "chinese"}
    if chinese:
        max_cps = 7.0 if children else 9.0
        max_chars = 16
        label = "zh-Hans"
    else:
        max_cps = 17.0 if children else 20.0
        max_chars = 42
        label = "en"
    return ErgonomicsRules(
        language=label,
        max_cps=max_cps,
        max_chars_line=max_chars,
        min_duration=DEFAULT_MIN_DURATION,
        max_duration=DEFAULT_MAX_DURATION,
        min_gap=DEFAULT_MIN_GAP_FRAMES / max(1.0, fps),
        children=children,
    )


def visible_lines(text: str) -> list[str]:
    """Strip ASS override tags and spacer lines, keeping the visible text."""
    without_tags = ASS_TAG.sub("", text)
    lines = []
    for part in re.split(r"\\N|\r?\n", without_tags):
        cleaned = part.replace("\\h", " ").strip()
        if cleaned:
            lines.append(re.sub(r"\s+", " ", cleaned))
    return lines


def _is_cjk(char: str) -> bool:
    return bool(CJK.match(char))


def weighted_char_count(text: str, language: str) -> float:
    """Count characters the way Netflix measures reading speed.

    For character-based languages, spaces/punctuation and half-width characters
    count 0.5. For English every character, including spaces, counts 1.
    """
    if not language.startswith("zh"):
        return float(len(text))
    total = 0.0
    for char in text:
        total += 1.0 if _is_cjk(char) else 0.5
    return total


def line_language(text: str) -> str:
    """Classify a mixed bilingual line by its dominant script."""
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return "en"
    cjk = sum(1 for char in visible if _is_cjk(char))
    return "zh-Hans" if cjk / len(visible) >= 0.3 else "en"


def _cue_from_ass(cue: dict) -> dict:
    return {
        "start": cue["start"],
        "end": cue["end"],
        "text": cue["text"],
    }


def load_cues(path: Path) -> list[dict]:
    extension = path.suffix.lower().lstrip(".")
    if extension in {"ass", "ssa"}:
        cues = subtitle_timing.parse_ass_cues(path)
        return [
            _cue_from_ass(cue)
            for cue in cues
            if not cue.get("drawing")
        ]
    return captions.parse_caption_file(path)


def analyze_cues(
    cues: Sequence[dict],
    rules: ErgonomicsRules,
    *,
    fps: float = 24.0,
    max_cps: float | None = None,
) -> dict:
    """Measure reading speed, duration, line length and gaps for every cue."""
    if max_cps is not None:
        rules = replace(rules, max_cps=float(max_cps))
    ordered = sorted(cues, key=lambda cue: float(cue["start"]))
    issues: list[dict] = []
    measured: list[dict] = []
    cps_values: list[float] = []
    for index, cue in enumerate(ordered):
        start = float(cue["start"])
        end = float(cue["end"])
        duration = end - start
        lines = visible_lines(str(cue.get("text", "")))
        line_records = []
        for line_index, line in enumerate(lines):
            language = line_language(line)
            line_rules = rules_for(language, fps=fps, children=rules.children)
            limit_cps = float(max_cps) if max_cps is not None else line_rules.max_cps
            limit_chars = line_rules.max_chars_line
            char_count = weighted_char_count(line, language)
            # Sliver events (a few milliseconds) are a timing defect, not a
            # meaningful reading-speed sample; duration_short reports them.
            cps = char_count / duration if duration >= 0.05 else 0.0
            if duration >= 0.05:
                cps_values.append(cps)
            line_records.append(
                {
                    "index": line_index,
                    "text": line,
                    "language":language,
                    "characters": round(char_count, 2),
                    "cps": round(cps, 2),
                    "limit_cps": limit_cps,
                    "limit_chars": limit_chars,
                }
            )
            if duration >= 0.05 and cps > limit_cps:
                severity = "error" if cps > limit_cps * 1.25 else "warning"
                issues.append(
                    _issue(
                        "reading_speed",
                        severity,
                        index,
                        start,
                        end,
                        f"{language} line {line_index + 1} reads at {cps:.1f} CPS "
                        f"(limit {limit_cps:g})",
                        measured=round(cps, 2),
                        limit=limit_cps,
                        text=line,
                    )
                )
            if len(line) > limit_chars:
                issues.append(
                    _issue(
                        "line_length",
                        "warning",
                        index,
                        start,
                        end,
                        f"{language} line {line_index + 1} has {len(line)} characters "
                        f"(limit {limit_chars})",
                        measured=len(line),
                        limit=limit_chars,
                        text=line,
                    )
                )
        if len(lines) > rules.max_lines:
            issues.append(
                _issue(
                    "line_count",
                    "error",
                    index,
                    start,
                    end,
                    f"Cue has {len(lines)} lines (limit {rules.max_lines})",
                    measured=len(lines),
                    limit=rules.max_lines,
                )
            )
        if duration < rules.min_duration:
            issues.append(
                _issue(
                    "duration_short",
                    "error" if duration < rules.min_duration * 0.8 else "warning",
                    index,
                    start,
                    end,
                    f"Cue lasts {duration:.3f} s (minimum {rules.min_duration:.3f} s)",
                    measured=round(duration, 3),
                    limit=round(rules.min_duration, 3),
                )
            )
        if duration > rules.max_duration:
            issues.append(
                _issue(
                    "duration_long",
                    "warning",
                    index,
                    start,
                    end,
                    f"Cue lasts {duration:.3f} s (maximum {rules.max_duration:g} s)",
                    measured=round(duration, 3),
                    limit=rules.max_duration,
                )
            )
        measured.append(
            {
                "index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(duration, 3),
                "lines": line_records,
                "max_cps": round(max((line["cps"] for line in line_records), default=0.0), 2),
            }
        )
    for index in range(len(ordered) - 1):
        gap = float(ordered[index + 1]["start"]) - float(ordered[index]["end"])
        if 0 < gap < rules.min_gap:
            issues.append(
                _issue(
                    "gap_short",
                    "warning",
                    index,
                    float(ordered[index]["start"]),
                    float(ordered[index]["end"]),
                    f"Gap to the next cue is {gap * 1000:.0f} ms "
                    f"(minimum {rules.min_gap * 1000:.0f} ms)",
                    measured=round(gap, 3),
                    limit=round(rules.min_gap, 3),
                )
            )
        if gap < 0:
            issues.append(
                _issue(
                    "overlap",
                    "error",
                    index,
                    float(ordered[index]["start"]),
                    float(ordered[index]["end"]),
                    f"Cue overlaps the next cue by {-gap:.3f} s",
                    measured=round(-gap, 3),
                )
            )
    sorted_cps = sorted(cps_values)
    p95 = sorted_cps[min(len(sorted_cps) - 1, int(round(len(sorted_cps) * 0.95)) - 1)] if sorted_cps else 0.0
    by_code: dict[str, int] = {}
    for issue in issues:
        by_code[issue["code"]] = by_code.get(issue["code"], 0) + 1
    return {
        "module": "module2c-subtitle-ergonomics",
        "rules": {
            "language": rules.language,
            "max_cps": rules.max_cps,
            "max_chars_line": rules.max_chars_line,
            "max_lines": rules.max_lines,
            "min_duration": round(rules.min_duration, 3),
            "max_duration": rules.max_duration,
            "min_gap": round(rules.min_gap, 3),
            "children": rules.children,
            "fps": fps,
        },
        "summary": {
            "cues": len(ordered),
            "max_cps": round(max(cps_values), 2) if cps_values else 0.0,
            "p95_cps": round(p95, 2),
            "mean_cps": round(sum(cps_values) / len(cps_values), 2) if cps_values else 0.0,
            "violations": len(issues),
            "errors": sum(1 for issue in issues if issue["severity"] == "error"),
            "warnings": sum(1 for issue in issues if issue["severity"] == "warning"),
            "by_code": by_code,
        },
        "issues": issues,
        "cues": measured,
    }


def _issue(
    code: str,
    severity: str,
    cue_index: int,
    start: float,
    end: float,
    message: str,
    *,
    measured: float | int | None = None,
    limit: float | int | None = None,
    text: str | None = None,
) -> dict:
    issue = {
        "code": code,
        "severity": severity,
        "cue": cue_index,
        "start": round(start, 3),
        "end": round(end, 3),
        "message": message,
    }
    if measured is not None:
        issue["measured"] = measured
    if limit is not None:
        issue["limit"] = limit
    if text is not None:
        issue["text"] = text
    return issue


def analyze_subtitle(
    path: Path,
    *,
    fps: float = 24.0,
    language: str | None = None,
    children: bool = False,
    max_cps: float | None = None,
) -> dict:
    cues = load_cues(path)
    if language is None:
        sample = " ".join(str(cue.get("text", "")) for cue in cues[:20])
        language = line_language(sample)
    rules = rules_for(language, fps=fps, children=children)
    result = analyze_cues(cues, rules, fps=fps, max_cps=max_cps)
    result["source"] = str(path)
    result["format"] = path.suffix.lower().lstrip(".")
    return result


def write_report(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def summary_line(result: dict) -> str:
    summary = result["summary"]
    return (
        f"{summary['cues']} cues, max {summary['max_cps']} CPS, "
        f"p95 {summary['p95_cps']} CPS, {summary['errors']} errors, "
        f"{summary['warnings']} warnings"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check subtitle reading ergonomics.")
    parser.add_argument("subtitle", type=Path, help="ASS, SSA, SRT or VTT subtitle file.")
    parser.add_argument("--fps", type=float, default=24.0, help="Video frame rate (default: 24).")
    parser.add_argument("--language", help="Language code; inferred from the text when omitted.")
    parser.add_argument("--children", action="store_true", help="Use children's reading speed limits.")
    parser.add_argument("--max-cps", type=float, help="Override the reading speed limit.")
    parser.add_argument("--report", type=Path, help="Write the JSON report here.")
    parser.add_argument("--fail-on", choices=("error", "warning", "never"), default="never")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = analyze_subtitle(
            args.subtitle,
            fps=args.fps,
            language=args.language,
            children=args.children,
            max_cps=args.max_cps,
        )
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(summary_line(result))
    for issue in result["issues"][:20]:
        print(f"  [{issue['severity']}] {issue['code']}: {issue['message']}")
    if args.report:
        write_report(args.report, result)
    if args.fail_on == "error" and result["summary"]["errors"]:
        return 1
    if args.fail_on == "warning" and result["summary"]["violations"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Vision, contrast and layout metrics
# ---------------------------------------------------------------------------

# Coordinates are authored on the 1920x1080 ASS canvas and scale with the video.
ZONE_GEOMETRY = {
    "action_safe": (67, 38, 1853, 1042),      # 93% action safe
    "title_safe": (96, 54, 1824, 1026),       # 90% title safe
    "subtitle": (66, 883, 1852, 1022),
    "information": (66, 760, 1852, 883),      # chyron / lower third
    "logo_top_left": (0, 0, 260, 120),
    "logo_top_right": (1660, 0, 1920, 120),
    "logo_bottom_right": (1660, 960, 1920, 1080),
    "face_region": (480, 120, 1440, 760),
}
PLAY_RESOLUTION = (1920, 1080)


def zones(width: int = 1920, height: int = 1080) -> dict[str, tuple[int, int, int, int]]:
    """Scale the authored zone map onto the actual video frame."""
    scale_x = width / PLAY_RESOLUTION[0]
    scale_y = height / PLAY_RESOLUTION[1]
    scaled = {}
    for name, (left, top, right, bottom) in ZONE_GEOMETRY.items():
        scaled[name] = (
            round(left * scale_x),
            round(top * scale_y),
            round(right * scale_x),
            round(bottom * scale_y),
        )
    return scaled


def _srgb(value: float) -> float:
    channel = value / 255
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = (max(0, min(255, int(channel))) for channel in rgb)
    return 0.2126 * _srgb(red) + 0.7152 * _srgb(green) + 0.0722 * _srgb(blue)


def contrast_ratio(foreground: tuple[int, int, int], background: tuple[int, int, int]) -> float:
    """WCAG 2.x contrast ratio (1:1 to 21:1)."""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def ass_color_rgb(value: str) -> tuple[int, int, int]:
    """Convert ASS ``&HAABBGGRR`` to an RGB tuple."""
    text = value.strip().lstrip("&").lstrip("Hh").rstrip("&")
    text = text.rjust(8, "0")[-8:]
    try:
        blue, green, red = int(text[2:4], 16), int(text[4:6], 16), int(text[6:8], 16)
    except ValueError as error:
        raise ValueError(f"Invalid ASS colour: {value!r}") from error
    return red, green, blue


def ass_color_alpha(value: str) -> int:
    text = value.strip().lstrip("&").lstrip("Hh").rstrip("&")
    text = text.rjust(8, "0")[-8:]
    try:
        return int(text[:2], 16)
    except ValueError as error:
        raise ValueError(f"Invalid ASS colour: {value!r}") from error


def parse_ass_styles(path: Path) -> dict:
    """Read PlayRes and the V4+ style table from an ASS file."""
    play_res = [1920, 1080]
    fields: list[str] | None = None
    styles: dict[str, dict] = {}
    in_styles = False
    in_info = False
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_styles = stripped.lower() in {"[v4+ styles]", "[v4 styles]"}
            in_info = stripped.lower() == "[script info]"
            fields = None
            continue
        if in_info and stripped.lower().startswith("playresx:"):
            play_res[0] = int(stripped.split(":", 1)[1].strip())
        elif in_info and stripped.lower().startswith("playresy:"):
            play_res[1] = int(stripped.split(":", 1)[1].strip())
        elif in_styles and stripped.lower().startswith("format:"):
            fields = [item.strip().lower() for item in stripped.split(":", 1)[1].split(",")]
        elif in_styles and stripped.lower().startswith("style:") and fields:
            values = stripped.split(":", 1)[1].lstrip().split(",", len(fields) - 1)
            if len(values) < len(fields):
                continue
            record = dict(zip(fields, values))
            styles[record.get("name", "").strip()] = {
                "font": record.get("fontname", "").strip(),
                "size": float(record.get("fontsize", 0) or 0),
                "primary": record.get("primarycolour", "&H00FFFFFF"),
                "outline_colour": record.get("outlinecolour", "&H00000000"),
                "back_colour": record.get("backcolour", "&H00000000"),
                "outline": float(record.get("outline", 0) or 0),
                "shadow": float(record.get("shadow", 0) or 0),
                "bold": int(float(record.get("bold", 0) or 0)),
            }
    return {"play_res": (play_res[0], play_res[1]), "styles": styles}


def style_metrics(parsed: dict) -> dict:
    """Summarize font size, script ratio, outline and colours for the layout report."""
    styles = parsed["styles"]
    play_res = parsed["play_res"]
    chinese = styles.get("Chinese") or {}
    english = styles.get("English") or {}
    chinese_size = float(chinese.get("size", 0))
    english_size = float(english.get("size", 0))
    ratio = round(chinese_size / english_size, 3) if english_size else None
    box = styles.get("CaptionBox") or {}
    return {
        "play_res": list(play_res),
        "chinese": {
            "font": chinese.get("font"),
            "size_px": chinese_size,
            "size_percent_height": round(chinese_size / play_res[1] * 100, 2) if play_res[1] else 0,
            "colour": ass_color_rgb(chinese.get("primary", "&H00FFFFFF")),
            "outline": chinese.get("outline"),
            "shadow": chinese.get("shadow"),
        },
        "english": {
            "font": english.get("font"),
            "size_px": english_size,
            "size_percent_height": round(english_size / play_res[1] * 100, 2) if play_res[1] else 0,
            "colour": ass_color_rgb(english.get("primary", "&H00FFFFFF")),
            "outline": english.get("outline"),
            "shadow": english.get("shadow"),
        },
        "chinese_to_english_ratio": ratio,
        "caption_box": (
            {
                "colour": ass_color_rgb(box.get("primary", "&H00000000")),
                "alpha": ass_color_alpha(box.get("primary", "&H00000000")),
                "opacity": round(1 - ass_color_alpha(box.get("primary", "&H00000000")) / 255, 3),
            }
            if box
            else None
        ),
    }
