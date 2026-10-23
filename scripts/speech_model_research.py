#!/usr/bin/env python3
"""Module 2b-2 - speech model research office.

Ranks the models published on the Hugging Face Open ASR Leaderboard (English
short-form), so model choice is based on measured WER and RTFx rather than
vendor claims. A dated CSV snapshot lives in
``docs/subtitles/data/open_asr_english_short_latest.csv``; ``--refresh`` pulls
the current file.

The leaderboard is English-only. Multilingual and Chinese quality must be
measured separately with the same audio; the repository's third-party
comparison tool records that evidence when API keys are available.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "subtitles" / "data" / "open_asr_english_short_latest.csv"
SOURCE_URL = (
    "https://huggingface.co/datasets/hf-audio/open-asr-leaderboard-results/raw/main/"
    "english_short_latest.csv"
)
SOURCE_PAGE = "https://huggingface.co/spaces/hf-audio/open_asr_leaderboard"


def _number(value: str | None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def load_rows(path: Path = SNAPSHOT) -> list[dict]:
    if not path.is_file():
        raise ValueError(f"ASR leaderboard snapshot not found: {path}")
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            wer = _number(raw.get("avg"))
            if wer is None:
                continue
            rows.append(
                {
                    "model": (raw.get("model") or "").strip(),
                    "wer": wer,
                    "rtfx": _number(raw.get("RTFx")),
                    "license": (raw.get("License") or "").strip(),
                    "size_b": _number(raw.get("Size (B)")),
                    "languages": _number(raw.get("# Languages")),
                    "encoder": (raw.get("Encoder") or "").strip(),
                    "decoder": (raw.get("Decoder") or "").strip(),
                }
            )
    if not rows:
        raise ValueError(f"No ranked models found in {path}")
    return rows


def rank(rows: list[dict], top: int = 15, *, open_weights_only: bool = False) -> list[dict]:
    filtered = [
        row
        for row in rows
        if not open_weights_only or row["license"] not in {"", "Proprietary", "NA"}
    ]
    return sorted(filtered, key=lambda row: (row["wer"], -(row["rtfx"] or 0)))[:top]


def as_markdown(rows: list[dict], *, title: str = "Open ASR Leaderboard (English short-form)") -> str:
    lines = [
        f"### {title}",
        "",
        "| Rank | Model | Avg WER | RTFx | License | Size (B) | Languages |",
        "|---:|---|---:|---:|---|---:|---:|",
    ]
    for index, row in enumerate(rows, 1):
        rtfx = f"{row['rtfx']:.1f}" if row["rtfx"] is not None else "—"
        size = f"{row['size_b']:g}" if row["size_b"] is not None else "—"
        languages = f"{row['languages']:g}" if row["languages"] is not None else "—"
        lines.append(
            f"| {index} | `{row['model']}` | {row['wer']:.2f}% | {rtfx} | "
            f"{row['license'] or '—'} | {size} | {languages} |"
        )
    lines += ["", f"Source: {SOURCE_PAGE} (snapshot CSV in `docs/subtitles/data/`).", ""]
    return "\n".join(lines)


def refresh(path: Path = SNAPSHOT) -> Path:
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "gitsynonyms-research/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rank speech models by Open ASR Leaderboard WER.")
    parser.add_argument("--top", type=int, default=15, help="Rows to show (default: 15).")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    parser.add_argument("--markdown", action="store_true", help="Print the Markdown table.")
    parser.add_argument("--open-weights", action="store_true", help="Hide proprietary services.")
    parser.add_argument("--refresh", action="store_true", help="Download the current leaderboard CSV.")
    parser.add_argument("--csv", type=Path, default=SNAPSHOT, help="Snapshot CSV to read.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.refresh:
            refresh(args.csv)
        rows = rank(load_rows(args.csv), args.top, open_weights_only=args.open_weights)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    elif args.markdown:
        print(as_markdown(rows))
    else:
        print(f"{'rank':>4}  {'model':52} {'WER':>7} {'RTFx':>9}  license")
        for index, row in enumerate(rows, 1):
            rtfx = f"{row['rtfx']:.1f}" if row["rtfx"] is not None else "-"
            print(f"{index:>4}  {row['model']:52} {row['wer']:>6.2f}% {rtfx:>9}  {row['license']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
