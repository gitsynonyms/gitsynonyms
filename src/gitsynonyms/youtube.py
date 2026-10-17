from __future__ import annotations

import argparse
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List, Sequence
from urllib.parse import parse_qs, urlparse

from deep_translator import GoogleTranslator
from deep_translator.exceptions import TooManyRequests
from youtube_transcript_api import YouTubeTranscriptApi


def extract_video_id(url: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url or ""):
        return url
    parsed = urlparse(url)
    if parsed.netloc and "youtube.com" in parsed.netloc:
        if parsed.path.startswith("/watch"):
            return parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[-1]
        if parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[-1]
    if parsed.netloc and "youtu.be" in parsed.netloc:
        return parsed.path.strip("/").split("/")[-1]
    raise ValueError(f"Unsupported YouTube URL or ID: {url!r}")


def fetch_transcript(video_url: str, language: str = "en") -> List[dict]:
    video_id = extract_video_id(video_url)
    transcript = YouTubeTranscriptApi().fetch(video_id, languages=[language])
    return [{"start": item.start, "duration": item.duration, "text": item.text} for item in transcript]


def _format_timestamp(seconds: float) -> str:
    td = timedelta(seconds=seconds)
    total_ms = int((seconds - int(seconds)) * 1000)
    hours, remainder = divmod(int(td.total_seconds()), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{total_ms:03d}"


def translate_text(text: str, *, target_lang: str = "zh-CN", translator=None) -> str:
    if not text:
        return ""
    for attempt in range(3):
        try:
            if translator is None:
                translator = GoogleTranslator(source="en", target=target_lang)
            return str(translator.translate(text))
        except TooManyRequests:
            if attempt < 2:
                time.sleep(1 + attempt)
                continue
            return text
        except Exception:
            return text
    return text


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


def save_bilingual_srt(video_url: str, output_path: str | Path, *, target_lang: str = "zh-CN") -> str:
    entries = fetch_transcript(video_url)
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
    args = parser.parse_args(argv)
    path = save_bilingual_srt(args.url, args.output, target_lang=args.lang)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
