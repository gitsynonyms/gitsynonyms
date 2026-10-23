import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "subtitle_verification.py"
SPEC = importlib.util.spec_from_file_location("subtitle_verification", SCRIPT)
verification = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verification
SPEC.loader.exec_module(verification)


def test_fuse_cues_prefers_youtube_when_sources_agree():
    cues_y = [
        verification.SubtitleCue(start_ns=1_000_000_000, end_ns=3_000_000_000, text="Hello", source="youtube-7fmt"),
    ]
    cues_a = [
        verification.SubtitleCue(start_ns=1_000_000_000, end_ns=3_000_000_000, text="Hello", source="assemblyai"),
    ]
    fused, conflicts = verification.fuse_cues({"youtube-7fmt": cues_y, "assemblyai": cues_a})
    assert len(fused) == 1
    assert fused[0].text == "Hello"
    assert len(conflicts) == 0


def test_fuse_cues_records_text_conflict():
    cues_y = [
        verification.SubtitleCue(start_ns=1_000_000_000, end_ns=3_000_000_000, text="Hello", source="youtube-7fmt"),
    ]
    cues_a = [
        verification.SubtitleCue(start_ns=1_000_000_000, end_ns=3_000_000_000, text="Hello world", source="assemblyai"),
    ]
    fused, conflicts = verification.fuse_cues({"youtube-7fmt": cues_y, "assemblyai": cues_a})
    assert any(c["type"] == "text_mismatch" for c in conflicts)
    assert fused[0].text == "Hello"  # YouTube 优先


def test_fuse_cues_records_word_timing_conflict():
    cues_y = [
        verification.SubtitleCue(
            start_ns=1_000_000_000, end_ns=3_000_000_000,
            text="Hello",
            words=[{"text": "Hello", "start_ns": 1_000_000_000, "end_ns": 2_000_000_000}],
            source="youtube-7fmt"
        ),
    ]
    cues_a = [
        verification.SubtitleCue(
            start_ns=1_000_000_000, end_ns=3_000_000_000,
            text="Hello",
            words=[{"text": "Hello", "start_ns": 1_100_000_000, "end_ns": 2_000_000_000}],
            source="assemblyai"
        ),
    ]
    fused, conflicts = verification.fuse_cues({"youtube-7fmt": cues_y, "assemblyai": cues_a})
    assert any(c["type"] == "word_timing_mismatch" for c in conflicts)


def test_verification_report_serializes():
    cues = [
        verification.SubtitleCue(start_ns=1_000_000_000, end_ns=3_000_000_000, text="Test", source="youtube-7fmt")
    ]
    report = verification.VerificationReport(
        video_id="TEST",
        title="Test Video",
        generated_at="2026-10-23T00:00:00Z",
        sources={"youtube-7fmt": {"cues": 1, "stats": {"cues": 1}}},
        fused_cues=[c.to_dict() for c in cues],
        conflicts=[],
        ergonomics={},
        ass_path="/tmp/test.ass",
        timing_path="/tmp/TIMING.json",
    )
    json_str = report.to_json()
    data = json.loads(json_str)
    assert data["video_id"] == "TEST"
    assert data["fused_cues"][0]["text"] == "Test"


def test_cue_to_dict():
    cue = verification.SubtitleCue(
        start_ns=1_000_000_000,
        end_ns=2_000_000_000,
        text="Hello",
        words=[{"text": "Hello", "start_ns": 1_000_000_000, "end_ns": 2_000_000_000}],
        source="youtube-7fmt",
        confidence=0.9,
    )
    d = cue.to_dict()
    assert d["start_ns"] == 1_000_000_000
    assert d["end_ns"] == 2_000_000_000
    assert d["text"] == "Hello"
    assert d["source"] == "youtube-7fmt"
    assert d["confidence"] == 0.9
    assert len(d["words"]) == 1


def test_cue_properties():
    cue = verification.SubtitleCue(start_ns=1_000_000_000, end_ns=3_000_000_000, text="Test")
    assert cue.start_s == 1.0
    assert cue.end_s == 3.0
    assert cue.duration_s == 2.0
