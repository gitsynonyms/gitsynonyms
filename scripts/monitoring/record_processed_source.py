#!/usr/bin/env python3
"""Append completed sources to the backward-compatible JSONL processing ledger."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{9}Z$")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def now_ns() -> tuple[int, str]:
    epoch_ns = time.time_ns()
    seconds, nanoseconds = divmod(epoch_ns, 1_000_000_000)
    iso = datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return epoch_ns, f"{iso}.{nanoseconds:09d}Z"


def legacy_record(line: str) -> dict | None:
    value = line.strip()
    if not value or value.startswith("#"):
        return None
    if value.startswith("{"):
        record = json.loads(value)
        if not isinstance(record, dict) or not record.get("source_id"):
            raise ValueError("Ledger JSON records must be objects with a source_id.")
        record.setdefault("schema_version", 1)
        record.setdefault("record_type", "source_archive")
        record.setdefault("source_platform", "unknown")
        record.setdefault("source_channel_id", None)
        record.setdefault("source_channel", None)
        record.setdefault("title", None)
        record.setdefault("source_url", None)
        record.setdefault("published_at", None)
        record.setdefault("published_epoch_ns", None)
        record.setdefault("published_date", None)
        record.setdefault("published_time_source", None)
        record.setdefault("published_time_precision", "unknown")
        record.setdefault("recorded_at", None)
        record.setdefault("processing_status", "published")
        record.setdefault("artifacts", [])
        return record
    if not VIDEO_ID_RE.fullmatch(value):
        raise ValueError(f"Unrecognized legacy ledger entry: {value!r}")
    return {
        "schema_version": 1,
        "record_type": "source_archive",
        "source_platform": "youtube",
        "source_id": value,
        "source_channel_id": None,
        "source_channel": None,
        "title": None,
        "source_url": f"https://www.youtube.com/watch?v={value}",
        "published_at": None,
        "published_epoch_ns": None,
        "published_date": None,
        "published_time_source": None,
        "published_time_precision": "unknown",
        "recorded_at": None,
        "processing_status": "published",
        "artifacts": [],
        "migration_note": "Migrated from legacy ID-only record; original metadata unavailable.",
    }


def read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = legacy_record(line)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"Invalid ledger entry at line {line_number}: {error}") from error
        if record is not None:
            records.append(record)
    return records


def validate_record(record: dict) -> None:
    required = (
        "source_platform",
        "source_id",
        "title",
        "source_url",
        "published_date",
        "published_time_precision",
        "recorded_at",
        "processing_status",
    )
    missing = [key for key in required if key not in record]
    if missing:
        raise ValueError(f"Ledger record missing required fields: {', '.join(missing)}")
    if not isinstance(record["source_platform"], str) or not record["source_platform"].strip():
        raise ValueError("source_platform must be a non-empty string.")
    if not isinstance(record["source_id"], str) or not record["source_id"].strip():
        raise ValueError("source_id must be a non-empty string.")
    if not isinstance(record["source_url"], str):
        raise ValueError("source_url must be a complete HTTP or HTTPS URL.")
    parsed_url = urlparse(record["source_url"])
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("source_url must be a complete HTTP or HTTPS URL.")
    for key in ("published_at", "recorded_at"):
        value = record[key]
        if value is not None and not TIMESTAMP_RE.fullmatch(value):
            raise ValueError(f"{key} must be a UTC timestamp with nine fractional digits.")
        if value is not None:
            try:
                datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError as error:
                raise ValueError(f"{key} must contain a valid UTC date and time.") from error
    published_date = record["published_date"]
    if published_date is not None:
        try:
            datetime.strptime(published_date, "%Y-%m-%d")
        except (TypeError, ValueError) as error:
            raise ValueError("published_date must be a valid YYYY-MM-DD date or null.") from error
    epoch_ns = record.get("published_epoch_ns")
    if epoch_ns is not None and not isinstance(epoch_ns, int):
        raise ValueError("published_epoch_ns must be an integer or null.")
    if record.get("published_at") is None and epoch_ns is not None:
        raise ValueError("published_epoch_ns must be null when published_at is unknown.")
    if record.get("published_at") is not None and epoch_ns is None:
        raise ValueError("published_epoch_ns is required when published_at is known.")
    if record.get("published_at") is not None:
        published_at = record["published_at"]
        published_seconds = int(
            datetime.strptime(published_at[:19], "%Y-%m-%dT%H:%M:%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        expected_epoch_ns = published_seconds * 1_000_000_000 + int(published_at[20:29])
        if epoch_ns != expected_epoch_ns:
            raise ValueError("published_epoch_ns must match published_at.")
        if published_date != published_at[:10]:
            raise ValueError("published_date must match the date in published_at.")
    if not isinstance(record["artifacts"], list):
        raise ValueError("artifacts must be a JSON list.")


def append_record(path: Path, record: dict) -> bool:
    validate_record(record)
    records = read_records(path)
    for index, existing in enumerate(records):
        if (
            existing.get("source_platform") == record["source_platform"]
            and existing.get("source_id") == record["source_id"]
        ):
            if existing.get("migration_note"):
                record["recorded_at"] = record.get("recorded_at") or existing.get("recorded_at")
                records[index] = record
                break
            return False
    else:
        records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
        newline="\n",
    )
    return True


def record_from_args(args: argparse.Namespace) -> dict:
    _, recorded_at = now_ns()
    try:
        artifacts = json.loads(args.artifacts)
    except json.JSONDecodeError as error:
        raise ValueError("artifacts must be valid JSON.") from error
    if not isinstance(artifacts, list):
        raise ValueError("artifacts must be a JSON list.")
    epoch_ns = args.published_epoch_ns
    record = {
        "schema_version": 1,
        "record_type": "source_archive",
        "source_platform": args.source_platform,
        "source_id": args.source_id,
        "source_channel_id": args.source_channel_id or None,
        "source_channel": args.source_channel or None,
        "title": args.title or None,
        "source_url": args.source_url,
        "published_at": args.published_at or None,
        "published_epoch_ns": epoch_ns,
        "published_date": args.published_date or None,
        "published_time_source": args.published_time_source or None,
        "published_time_precision": args.published_time_precision,
        "recorded_at": recorded_at,
        "processing_status": "published",
        "artifacts": artifacts,
    }
    return record


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--source-platform", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-channel-id", default="")
    parser.add_argument("--source-channel", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--published-at", default="")
    parser.add_argument("--published-epoch-ns", type=int)
    parser.add_argument("--published-date", default="")
    parser.add_argument("--published-time-source", default="")
    parser.add_argument("--published-time-precision", default="unknown")
    parser.add_argument("--artifacts", default="[]")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        record = record_from_args(args)
        added = append_record(args.state_file, record)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if added:
        print(f"Recorded {record['source_platform']} source {record['source_id']}.")
    else:
        print(f"Source already recorded: {record['source_platform']} {record['source_id']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
