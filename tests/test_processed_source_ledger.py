import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "monitoring" / "record_processed_source.py"
SPEC = importlib.util.spec_from_file_location("record_processed_source", SCRIPT)
ledger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ledger)


def source_record(**overrides):
    record = {
        "schema_version": 1,
        "record_type": "source_archive",
        "source_platform": "youtube",
        "source_id": "HEWlaWY9Qcs",
        "source_channel_id": "UCYxRlFDqcWM4y7FfpiAN3KQ",
        "source_channel": "The White House",
        "title": "White House video",
        "source_url": "https://www.youtube.com/watch?v=HEWlaWY9Qcs",
        "published_at": "2026-10-12T16:00:00.000000000Z",
        "published_epoch_ns": 1791820800000000000,
        "published_date": "2026-10-12",
        "published_time_source": "timestamp",
        "published_time_precision": "second",
        "recorded_at": "2026-10-23T08:00:00.000000000Z",
        "processing_status": "published",
        "artifacts": [],
    }
    record.update(overrides)
    return record


def test_read_records_migrates_legacy_ids_and_keeps_json_records(tmp_path):
    path = tmp_path / "whitehouse-processed-videos.txt"
    record = source_record()
    path.write_text(
        "# Legacy entries\nHEWlaWY9Qcs\n" + json.dumps(record) + "\n",
        encoding="utf-8",
    )

    records = ledger.read_records(path)

    assert len(records) == 2
    assert records[0]["source_platform"] == "youtube"
    assert records[0]["source_url"].endswith("v=HEWlaWY9Qcs")
    assert records[0]["title"] is None
    assert records[0]["published_time_precision"] == "unknown"
    assert records[1]["title"] == "White House video"


def test_append_record_rewrites_legacy_entries_and_adds_generic_source(tmp_path):
    path = tmp_path / "processed.txt"
    path.write_text("IySF_8OArvw\n", encoding="utf-8")
    record = source_record(
        source_platform="vimeo",
        source_id="123456",
        source_channel="Example account",
        source_url="https://vimeo.com/123456",
        published_at=None,
        published_epoch_ns=None,
        published_time_source=None,
        published_time_precision="unknown",
    )

    assert ledger.append_record(path, record)
    serialized = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert [item["source_id"] for item in serialized] == ["IySF_8OArvw", "123456"]
    assert serialized[1]["source_platform"] == "vimeo"
    assert serialized[1]["source_url"] == "https://vimeo.com/123456"


def test_append_record_is_idempotent_by_platform_and_source_id(tmp_path):
    path = tmp_path / "processed.txt"
    record = source_record()

    assert ledger.append_record(path, record)
    assert not ledger.append_record(path, record)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_append_record_enriches_a_legacy_id_instead_of_skipping_it(tmp_path):
    path = tmp_path / "processed.txt"
    path.write_text("HEWlaWY9Qcs\n", encoding="utf-8")
    record = source_record()

    assert ledger.append_record(path, record)
    serialized = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert len(serialized) == 1
    assert serialized[0]["title"] == "White House video"
    assert "migration_note" not in serialized[0]


def test_validation_requires_nine_fractional_digits_and_complete_url():
    record = source_record()
    ledger.validate_record(record)

    for timestamp in ("2026-10-12T16:00:00Z", "2026-10-12T16:00:00.000Z"):
        try:
            ledger.validate_record({**record, "published_at": timestamp})
        except ValueError as error:
            assert "nine fractional digits" in str(error)
        else:
            raise AssertionError("Expected timestamp precision validation to fail")

    try:
        ledger.validate_record({**record, "source_url": "HEWlaWY9Qcs"})
    except ValueError as error:
        assert "complete HTTP or HTTPS URL" in str(error)
    else:
        raise AssertionError("Expected incomplete source URL to fail")


def test_validation_rejects_epoch_without_a_known_publication_timestamp():
    try:
        ledger.validate_record(
            source_record(
                published_at=None,
                published_epoch_ns=1791820800000000000,
                published_time_precision="unknown",
            )
        )
    except ValueError as error:
        assert "must be null when published_at is unknown" in str(error)
    else:
        raise AssertionError("Expected inconsistent publication metadata to fail")
