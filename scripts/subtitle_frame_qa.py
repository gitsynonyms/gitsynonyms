#!/usr/bin/env python3
"""Run OCR and layout checks on every frame in a subtitle-dense 30-second clip."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import subtitle_ergonomics  # noqa: E402
import subtitle_timing  # noqa: E402

SAMPLE_SECONDS = 30.0
MIN_TEXT_HEIGHT_RATIO = 0.02
SAFE_MARGIN_RATIO = 0.03
MAX_TEXT_WIDTH_RATIO = 0.90


def _normalized_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _caption_coverage(cues: Sequence[dict], start: float, end: float) -> float:
    intervals = sorted(
        (
            max(start, float(cue["start"])),
            min(end, float(cue["end"])),
        )
        for cue in cues
        if float(cue["end"]) > start and float(cue["start"]) < end
    )
    covered = 0.0
    current_start = current_end = None
    for interval_start, interval_end in intervals:
        if current_end is None:
            current_start, current_end = interval_start, interval_end
        elif interval_start <= current_end:
            current_end = max(current_end, interval_end)
        else:
            covered += current_end - current_start
            current_start, current_end = interval_start, interval_end
    if current_end is not None:
        covered += current_end - current_start
    return covered


def select_sample_window(
    cues: Sequence[dict], video_duration: float, sample_seconds: float = SAMPLE_SECONDS
) -> tuple[float, float]:
    """Select the 30-second window with the most subtitle-on-screen time."""
    if not math.isfinite(video_duration) or video_duration < sample_seconds:
        raise ValueError(f"Video must be at least {sample_seconds:g} seconds long.")
    latest_start = video_duration - sample_seconds
    candidates = {0.0, latest_start}
    for cue in cues:
        for boundary in (float(cue["start"]), float(cue["end"]) - sample_seconds):
            candidates.add(min(latest_start, max(0.0, boundary)))
    start = max(
        candidates,
        key=lambda candidate: (
            _caption_coverage(cues, candidate, candidate + sample_seconds),
            -candidate,
        ),
    )
    return start, start + sample_seconds


def parse_tesseract_tsv(tsv: str) -> list[dict]:
    """Combine Tesseract word records into visible text lines."""
    grouped: dict[tuple[str, str, str, str], list[dict]] = {}
    for row in csv.DictReader(tsv.splitlines(), delimiter="\t"):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            confidence = float(row["conf"])
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if confidence < 0 or width <= 0 or height <= 0:
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        grouped.setdefault(key, []).append(
            {
                "text": row["text"].strip(),
                "confidence": confidence,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
            }
        )

    lines = []
    for words in grouped.values():
        lines.append(
            {
                "text": " ".join(word["text"] for word in words),
                "confidence": sum(word["confidence"] for word in words) / len(words),
                "left": min(word["left"] for word in words),
                "top": min(word["top"] for word in words),
                "right": max(word["right"] for word in words),
                "bottom": max(word["bottom"] for word in words),
            }
        )
    return lines


def analyze_frames(
    frames: Sequence[dict], width: int, height: int, cues: Sequence[dict], start: float
) -> dict:
    issues = []
    visible_lines = 0
    confidence_values = []
    repeated_caption_keys: dict[str, dict] = {}
    sample_end = start + SAMPLE_SECONDS

    for cue_index, cue in enumerate(sorted(cues, key=lambda item: float(item["start"]))):
        cue_start, cue_end = float(cue["start"]), float(cue["end"])
        if cue_end <= start or cue_start >= sample_end:
            continue
        for text in subtitle_ergonomics.visible_lines(str(cue.get("text", ""))):
            key = _normalized_text(text)
            if not key:
                continue
            prior = repeated_caption_keys.get(key)
            if prior and cue_start - prior["end"] <= 1.0:
                issues.append(
                    {
                        "code": "repeated_caption",
                        "severity": "warning",
                        "start": round(cue_start, 3),
                        "text": text,
                        "previous_cue": prior["index"],
                    }
                )
            repeated_caption_keys[key] = {
                "end": cue_end,
                "index": cue_index,
            }

    for frame in frames:
        lines = [
            line
            for line in frame["lines"]
            if line["top"] + line["bottom"] >= height * 0.9
        ]
        visible_lines += len(lines)
        normalized_in_frame: dict[str, dict] = {}
        for line in lines:
            normalized = _normalized_text(line["text"])
            if not normalized:
                continue
            frame_location = {
                "frame": frame["index"],
                "timestamp_seconds": frame.get("timestamp_seconds"),
            }
            if normalized in normalized_in_frame:
                issues.append(
                    {
                        "code": "duplicate_visible_text",
                        "severity": "error",
                        **frame_location,
                        "text": line["text"],
                    }
                )
            normalized_in_frame[normalized] = line
            confidence_values.append(line["confidence"])

            text_height = line["bottom"] - line["top"]
            text_width = line["right"] - line["left"]
            if (
                line["left"] < width * SAFE_MARGIN_RATIO
                or line["right"] > width * (1 - SAFE_MARGIN_RATIO)
                or line["top"] < height * SAFE_MARGIN_RATIO
                or line["bottom"] > height * (1 - SAFE_MARGIN_RATIO)
            ):
                issues.append(
                    {
                        "code": "unsafe_screen_margin",
                        "severity": "error",
                        **frame_location,
                        "text": line["text"],
                    }
                )
            if text_height < height * MIN_TEXT_HEIGHT_RATIO:
                issues.append(
                    {
                        "code": "text_too_small",
                        "severity": "error",
                        **frame_location,
                        "text": line["text"],
                        "height_px": text_height,
                        "minimum_px": round(height * MIN_TEXT_HEIGHT_RATIO, 1),
                    }
                )
            if text_width > width * MAX_TEXT_WIDTH_RATIO:
                issues.append(
                    {
                        "code": "text_too_wide",
                        "severity": "warning",
                        **frame_location,
                        "text": line["text"],
                        "width_ratio": round(text_width / width, 4),
                    }
                )
            if line["confidence"] < 35:
                issues.append(
                    {
                        "code": "low_ocr_confidence",
                        "severity": "warning",
                        **frame_location,
                        "text": line["text"],
                        "confidence": round(line["confidence"], 1),
                    }
                )

    if not visible_lines:
        issues.append(
            {
                "code": "no_captions_detected",
                "severity": "error",
                "message": "OCR did not find subtitle text in the sampled caption area.",
            }
        )
    errors = sum(issue["severity"] == "error" for issue in issues)
    warnings = sum(issue["severity"] == "warning" for issue in issues)
    return {
        "status": "failed" if errors else "passed_with_warnings" if warnings else "passed",
        "sample": {
            "start_seconds": round(start, 3),
            "end_seconds": round(sample_end, 3),
            "duration_seconds": SAMPLE_SECONDS,
            "frames_analyzed": len(frames),
            "video_width": width,
            "video_height": height,
        },
        "metrics": {
            "recognized_caption_lines": visible_lines,
            "mean_ocr_confidence": (
                round(sum(confidence_values) / len(confidence_values), 1)
                if confidence_values
                else None
            ),
            "limits": {
                "safe_margin_percent": SAFE_MARGIN_RATIO * 100,
                "minimum_text_height_percent": MIN_TEXT_HEIGHT_RATIO * 100,
                "maximum_text_width_percent": MAX_TEXT_WIDTH_RATIO * 100,
            },
        },
        "summary": {"errors": errors, "warnings": warnings},
        "issues": issues,
    }


def _run(command: Sequence[str], *, text: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command), check=True, capture_output=True, text=text
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"Required executable was not found: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        details = error.stderr if isinstance(error.stderr, str) else "command failed"
        raise RuntimeError(f"{command[0]} failed: {details}") from error


def _probe_video(ffprobe: str, video: Path) -> dict:
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ]
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise ValueError(f"No video stream found in {video}.")
    stream = streams[0]
    try:
        duration = float(data["format"]["duration"])
        width, height = int(stream["width"]), int(stream["height"])
        numerator, denominator = (float(part) for part in stream["avg_frame_rate"].split("/", 1))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"Video metadata is incomplete for {video}.") from error
    fps = numerator / denominator if denominator else 0
    if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0 or fps <= 0:
        raise ValueError(f"Video metadata is invalid for {video}.")
    return {"duration": duration, "width": width, "height": height, "fps": fps}


def _ocr_frame(frame_path: Path, tesseract: str, language: str) -> str:
    result = _run(
        [
            tesseract,
            str(frame_path),
            "stdout",
            "-l",
            language,
            "--psm",
            "6",
            "tsv",
        ]
    )
    return result.stdout


def run_video_qa(
    video: Path,
    subtitle: Path,
    report_path: Path,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    tesseract: str = "tesseract",
    language: str = "eng",
) -> dict:
    if not video.is_file():
        raise ValueError(f"Video file does not exist: {video}")
    if not subtitle.is_file():
        raise ValueError(f"Subtitle file does not exist: {subtitle}")
    metadata = _probe_video(ffprobe, video)
    cues = [
        cue
        for cue in subtitle_timing.parse_ass_cues(subtitle)
        if not cue["drawing"] and cue["end"] > cue["start"]
    ]
    if not cues:
        raise ValueError(f"ASS file contains no timed text cues: {subtitle}")
    sample_start, _ = select_sample_window(cues, metadata["duration"])
    languages = _run([tesseract, "--list-langs"]).stdout.splitlines()
    available_languages = {line.strip() for line in languages}
    required_languages = set(language.split("+"))
    missing_languages = required_languages - available_languages
    if missing_languages:
        raise RuntimeError(
            "Tesseract is missing OCR language data: " + ", ".join(sorted(missing_languages))
        )

    with tempfile.TemporaryDirectory(prefix="subtitle-frame-qa-") as temporary:
        frame_dir = Path(temporary)
        frame_pattern = frame_dir / "frame_%06d.jpg"
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{sample_start:.3f}",
                "-i",
                str(video),
                "-t",
                f"{SAMPLE_SECONDS:g}",
                "-map",
                "0:v:0",
                "-vsync",
                "0",
                "-q:v",
                "3",
                str(frame_pattern),
            ]
        )
        frame_paths = sorted(frame_dir.glob("frame_*.jpg"))
        if not frame_paths:
            raise RuntimeError("FFmpeg extracted no frames from the 30-second sample.")
        workers = max(1, min(4, os.cpu_count() or 1))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            tsv_outputs = list(
                executor.map(
                    lambda path: _ocr_frame(path, tesseract, language),
                    frame_paths,
                )
            )
        frames = [
            {
                "index": index,
                "timestamp_seconds": round(sample_start + index / metadata["fps"], 3),
                "lines": parse_tesseract_tsv(tsv),
            }
            for index, tsv in enumerate(tsv_outputs)
        ]

    report = analyze_frames(frames, metadata["width"], metadata["height"], cues, sample_start)
    report["module"] = "subtitle-frame-qa"
    report["version"] = 1
    report["video"] = str(video)
    report["subtitle"] = str(subtitle)
    report["ocr"] = {"engine": "Tesseract", "language": language}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OCR every frame in a subtitle-dense 30-second video sample."
    )
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--ass", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("FRAME_QA.json"))
    parser.add_argument("--language", default="eng")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe") or "ffprobe")
    parser.add_argument("--tesseract", default=shutil.which("tesseract") or "tesseract")
    args = parser.parse_args(argv)
    try:
        report = run_video_qa(
            args.video,
            args.ass,
            args.report,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            tesseract=args.tesseract,
            language=args.language,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Subtitle frame QA failed: {error}", file=sys.stderr)
        return 1
    print(
        "Subtitle frame QA: "
        f"{report['status']} ({report['sample']['frames_analyzed']} frames, "
        f"{report['summary']['errors']} errors, {report['summary']['warnings']} warnings)"
    )
    return 1 if report["summary"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
