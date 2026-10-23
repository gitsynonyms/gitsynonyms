import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "subtitle_ergonomics.py"
SPEC = importlib.util.spec_from_file_location("subtitle_ergonomics", SCRIPT)
subtitle_ergonomics = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = subtitle_ergonomics
SPEC.loader.exec_module(subtitle_ergonomics)


ASS = r"""[Script Info]
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize
Style: Chinese,Arial,57.6
Style: CaptionBox,Arial,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Chinese,,0,0,0,,你好\N{\fs10}\h\NHello
Dialogue: 0,0:00:00.00,0:00:10.00,CaptionBox,,0,0,0,,{\an7\pos(0,0)\p1}m 0 0 l 10 0 10 10 0 10{\p0}
"""


def cue(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_published_limits_are_used():
    english = subtitle_ergonomics.rules_for("en", fps=25)
    chinese = subtitle_ergonomics.rules_for("zh-Hans", fps=25)
    children = subtitle_ergonomics.rules_for("en", fps=25, children=True)

    assert english.max_cps == 20
    assert english.max_chars_line == 42
    assert chinese.max_cps == 9
    assert chinese.max_chars_line == 16
    assert children.max_cps == 17
    assert english.min_gap == 2 / 25
    assert english.min_duration == 20 / 24


def test_visible_lines_strips_ass_tags_and_the_spacer_line():
    lines = subtitle_ergonomics.visible_lines(r"{\fs10}\h\N你好\N{\fs10}\h\NHello world")

    assert lines == ["你好", "Hello world"]


def test_chinese_reading_speed_counts_half_width_characters_as_half():
    assert subtitle_ergonomics.weighted_char_count("你好A", "zh-Hans") == 2.5
    assert subtitle_ergonomics.weighted_char_count("Hello", "en") == 5
    assert subtitle_ergonomics.line_language("你好世界") == "zh-Hans"
    assert subtitle_ergonomics.line_language("Hello world") == "en"


def test_a_short_slow_cue_has_no_violations():
    result = subtitle_ergonomics.analyze_cues([cue(1, 3, "Hello")], subtitle_ergonomics.rules_for("en"))

    assert result["summary"]["violations"] == 0
    assert result["cues"][0]["max_cps"] == 2.5


def test_fast_english_text_is_a_reading_speed_error():
    result = subtitle_ergonomics.analyze_cues(
        [cue(1, 2, "a" * 30)], subtitle_ergonomics.rules_for("en")
    )

    issue = next(item for item in result["issues"] if item["code"] == "reading_speed")
    assert issue["severity"] == "error"
    assert issue["measured"] == 30
    assert issue["limit"] == 20


def test_fast_chinese_text_uses_the_nine_cps_limit():
    result = subtitle_ergonomics.analyze_cues(
        [cue(1, 2, "一二三四五六七八九十")], subtitle_ergonomics.rules_for("en")
    )

    issue = next(item for item in result["issues"] if item["code"] == "reading_speed")
    assert issue["limit"] == 9
    assert issue["severity"] == "warning"


def test_long_line_reports_the_character_limit():
    result = subtitle_ergonomics.analyze_cues(
        [cue(1, 5, "a" * 43)], subtitle_ergonomics.rules_for("en")
    )

    issue = next(item for item in result["issues"] if item["code"] == "line_length")
    assert issue["measured"] == 43
    assert issue["limit"] == 42


def test_short_duration_gap_overlap_and_line_count_are_reported():
    result = subtitle_ergonomics.analyze_cues(
        [
            cue(0, 0.4, "Hello"),
            cue(0.44, 2.0, "a\nb\nc"),
        ],
        subtitle_ergonomics.rules_for("en", fps=25),
    )

    codes = {issue["code"] for issue in result["issues"]}
    assert "duration_short" in codes
    assert "gap_short" in codes
    assert "line_count" in codes

    overlap = subtitle_ergonomics.analyze_cues(
        [cue(0, 2, "Hello"), cue(1.5, 3, "World")],
        subtitle_ergonomics.rules_for("en"),
    )
    assert any(issue["code"] == "overlap" for issue in overlap["issues"])


def test_ass_analysis_ignores_the_caption_box(tmp_path):
    path = tmp_path / "captions.ass"
    path.write_text(ASS, encoding="utf-8")

    result = subtitle_ergonomics.analyze_subtitle(path, fps=25)

    assert result["summary"]["cues"] == 1
    assert result["cues"][0]["lines"][0]["text"] == "你好"
    assert result["cues"][0]["lines"][1]["text"] == "Hello"


def test_cli_fails_on_errors_when_requested(tmp_path, capsys):
    path = tmp_path / "bad.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:01,400\nHello\n", encoding="utf-8")

    assert subtitle_ergonomics.main([str(path), "--fail-on", "error"]) == 1
    assert "duration_short" in capsys.readouterr().out


def test_report_is_json_serializable(tmp_path):
    result = subtitle_ergonomics.analyze_cues(
        [cue(1, 3, "Hello")], subtitle_ergonomics.rules_for("en")
    )
    path = tmp_path / "ERGONOMICS.json"

    subtitle_ergonomics.write_report(path, result)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["module"] == "module2c-subtitle-ergonomics"
    assert stored["summary"]["cues"] == 1
