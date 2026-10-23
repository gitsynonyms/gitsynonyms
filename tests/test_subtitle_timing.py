import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "subtitle_timing.py"
SPEC = importlib.util.spec_from_file_location("subtitle_timing", SCRIPT)
subtitle_timing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = subtitle_timing
SPEC.loader.exec_module(subtitle_timing)


JSON3_EN = {
    "events": [
        {
            "tStartMs": 1000,
            "dDurationMs": 2000,
            "segs": [{"utf8": "Hello"}, {"utf8": " world", "tOffsetMs": 500}],
        },
        {
            "tStartMs": 4000,
            "dDurationMs": 2000,
            "segs": [{"utf8": "Second"}, {"utf8": " line", "tOffsetMs": 600}],
        },
    ]
}

SRV3_EN = (
    '<?xml version="1.0" encoding="utf-8" ?><timedtext format="3"><body>'
    '<p t="1000" d="2000"><s t="0">Hello</s><s t="500"> world</s></p>'
    '<p t="4000" d="2000"><s t="0">Second</s><s t="600"> line</s></p>'
    "</body></timedtext>"
)

SRT_EN = "1\n00:00:01,000 --> 00:00:03,000\nHello world\n\n2\n00:00:04,000 --> 00:00:06,000\nSecond line\n"

JSON3_ZH = {
    "events": [
        {"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "你好"}, {"utf8": "世界", "tOffsetMs": 500}]},
        {"tStartMs": 4000, "dDurationMs": 2000, "segs": [{"utf8": "第二"}, {"utf8": "行", "tOffsetMs": 600}]},
    ]
}

SRT_ZH = "1\n00:00:01,000 --> 00:00:03,000\n你好世界\n\n2\n00:00:04,000 --> 00:00:06,000\n第二行\n"


def make_caption_dir(tmp_path, video_id="TEST", with_srv3=True):
    folder = tmp_path / "captions"
    folder.mkdir(exist_ok=True)
    (folder / f"{video_id}.en.en-orig.json3").write_text(json.dumps(JSON3_EN), encoding="utf-8")
    if with_srv3:
        (folder / f"{video_id}.en.en-orig.srv3").write_text(SRV3_EN, encoding="utf-8")
    (folder / f"{video_id}.en.en-orig.srt").write_text(SRT_EN, encoding="utf-8")
    (folder / f"{video_id}.en.zh-Hans.json3").write_text(json.dumps(JSON3_ZH), encoding="utf-8")
    (folder / f"{video_id}.en.zh-Hans.srt").write_text(SRT_ZH, encoding="utf-8")
    return folder


def test_discover_tracks_finds_every_published_format(tmp_path):
    folder = make_caption_dir(tmp_path)

    found = subtitle_timing.discover_tracks(folder, "TEST", "en-orig")

    assert list(found) == ["json3", "srv3", "srt"]
    assert found["json3"].name == "TEST.en.en-orig.json3"
    assert found["srt"].name == "TEST.en.en-orig.srt"


def test_discover_tracks_reads_legacy_names(tmp_path):
    folder = tmp_path / "legacy"
    folder.mkdir()
    (folder / "auto-generated_English.srt").write_text(SRT_EN, encoding="utf-8")
    (folder / "source_English.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello world\n", encoding="utf-8"
    )

    found = subtitle_timing.discover_tracks(folder, "TEST", "en-orig")

    assert found["srt"].name == "auto-generated_English.srt"
    assert found["vtt"].name == "source_English.vtt"


def test_fused_track_uses_json3_words_on_a_nanosecond_timebase(tmp_path):
    folder = make_caption_dir(tmp_path)
    files = subtitle_timing.discover_tracks(folder, "TEST", "en-orig")

    track = subtitle_timing.fuse_track("en-orig", subtitle_timing.parse_formats(files), files)

    assert track["timebase"] == "nanosecond"
    assert track["formats_used"] == ["json3", "srv3", "srt"]
    assert track["primary_format"] == "json3"
    first = track["cues"][0]
    assert first["start_ns"] == 1_000_000_000
    assert first["end_ns"] == 3_000_000_000
    assert first["text"] == "Hello world"
    assert [word["text"] for word in first["word_timings"]] == ["Hello", "world"]
    assert first["word_timings"][1]["start_ns"] == 1_500_000_000
    assert first["word_timings"][1]["end_ns"] == 3_000_000_000
    assert first["word_timings"][1]["sources"] == ["json3", "srv3"]


def test_fuse_values_keeps_json3_when_sources_agree():
    assert subtitle_timing.fuse_values([("srt", 1_010_000_000), ("json3", 1_000_000_000)]) == 1_000_000_000


def test_fuse_values_uses_the_weighted_median_on_a_real_disagreement():
    value = subtitle_timing.fuse_values(
        [
            ("json3", 1_000_000_000),
            ("srv3", 1_200_000_000),
            ("srv2", 1_100_000_000),
        ]
    )

    assert value == 1_100_000_000


def test_centisecond_quantization_is_bounded_to_five_milliseconds():
    for value in (1_000_000_000, 1_004_999_999, 1_005_000_000, 1_009_999_999):
        error = subtitle_timing.quantization_error_ms(value)
        assert 0 <= error <= 5
    assert subtitle_timing.quantize_centiseconds(1_004_999_999) == 100
    assert subtitle_timing.quantize_centiseconds(1_005_000_000) == 101


def test_build_tracks_merges_the_available_formats_for_both_languages(tmp_path):
    folder = make_caption_dir(tmp_path)

    tracks = subtitle_timing.build_tracks(folder, "TEST", ("zh-Hans", "en-orig"))

    assert tracks["zh-Hans"]["formats_used"] == ["json3", "srt"]
    assert tracks["en-orig"]["formats_used"] == ["json3", "srv3", "srt"]
    assert tracks["zh-Hans"]["cues"][0]["start_ns"] == 1_000_000_000
    assert tracks["en-orig"]["cues"][0]["word_timings"][1]["start_ns"] == 1_500_000_000


def test_missing_language_is_reported_with_its_code(tmp_path):
    folder = make_caption_dir(tmp_path)

    try:
        subtitle_timing.build_tracks(folder, "TEST", ("fr", "en-orig"))
    except ValueError as error:
        assert "fr" in str(error)
    else:
        raise AssertionError("a missing language must fail")


def test_precise_ass_without_a_box_contains_only_subtitle_events(tmp_path):
    folder = make_caption_dir(tmp_path)
    tracks = subtitle_timing.build_tracks(folder, "TEST", ("zh-Hans", "en-orig"))

    content, stats = subtitle_timing.build_precise_ass(
        tracks["zh-Hans"], tracks["en-orig"], box_style="none"
    )

    assert "CaptionBox" not in content
    assert "CaptionFacet" not in content
    assert "Dialogue: 1,0:00:01.00,0:00:03.00" in content
    assert "; timebase=nanosecond ass-resolution=centisecond" in content
    assert stats["ass_resolution"] == "centisecond"
    assert stats["word_timings"] == 11  # 7 Chinese characters + 4 English words


def test_precise_ass_uses_the_semi_transparent_presidential_blue_box(tmp_path):
    folder = make_caption_dir(tmp_path)
    tracks = subtitle_timing.build_tracks(folder, "TEST", ("zh-Hans", "en-orig"))

    content, _ = subtitle_timing.build_precise_ass(
        tracks["zh-Hans"],
        tracks["en-orig"],
        box_style="presidential-blue",
        box_opacity=0.85,
    )

    # #0F1647 at 85% opacity -> ASS alpha 0x26, BGR 47160F.
    assert "Style: CaptionBox,Arial,1,&H2647160F," in content
    assert content.count("CaptionBox") >= 2
    assert r"{\an7\pos(66,883)\p1}" in content


def test_timing_report_keeps_nanoseconds_and_quantization_statistics(tmp_path):
    folder = make_caption_dir(tmp_path)
    tracks = subtitle_timing.build_tracks(folder, "TEST", ("zh-Hans", "en-orig"))
    path = tmp_path / "TIMING.json"

    payload = subtitle_timing.write_timing_report(
        path, tracks, video_id="TEST", video=tmp_path / "video.mp4"
    )
    stored = json.loads(path.read_text(encoding="utf-8"))

    assert stored["timebase"] == "nanosecond"
    assert stored["ass_max_quantization_error_ms"] == 5.0
    assert stored["tracks"]["en-orig"]["cues"][0]["start_ns"] == 1_000_000_000
    assert isinstance(stored["tracks"]["en-orig"]["cues"][0]["start_ns"], int)
    assert stored["statistics"]["timestamps"] == payload["statistics"]["timestamps"]
    assert stored["statistics"]["ass_max_quantization_error_ms"] == 5.0


def test_precision_statistics_measure_the_centisecond_loss():
    track = {
        "language": "en-orig",
        "formats_used": ["json3"],
        "source_resolution": {"json3": "1 ms"},
        "cue_count": 1,
        "cues": [
            {
                "start_ns": 1_005_000_000,
                "end_ns": 2_000_000_000,
                "text": "x",
                "word_timings": [],
            }
        ],
    }

    stats = subtitle_timing.precision_stats({"en-orig": track})

    assert stats["measured_max_quantization_error_ms"] == 5.0
    assert stats["ass_max_quantization_error_ms"] == 5.0


def test_precision_vtt_keeps_millisecond_timestamps(tmp_path):
    track = {
        "language": "en-orig",
        "cues": [
            {
                "start_ns": 1_005_000_000,
                "end_ns": 2_500_000_000,
                "text": "Hello",
                "word_timings": [],
            }
        ],
    }
    path = tmp_path / "precision.en-orig.vtt"

    info = subtitle_timing.write_precision_vtt(track, path)

    content = path.read_text(encoding="utf-8")
    assert content.startswith("WEBVTT\n")
    assert "00:00:01.005 --> 00:00:02.500" in content
    assert info["resolution"] == "1 ms"
    assert info["cues"] == 1
    assert subtitle_timing.format_vtt_timestamp(3_600_000_000_000) == "01:00:00.000"
