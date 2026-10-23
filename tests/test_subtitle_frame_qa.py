import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "subtitle_frame_qa.py"
SPEC = importlib.util.spec_from_file_location("subtitle_frame_qa", SCRIPT)
subtitle_frame_qa = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = subtitle_frame_qa
SPEC.loader.exec_module(subtitle_frame_qa)


def tesseract_line(text, *, left=100, top=850, width=240, height=36, confidence=90):
    return {
        "text": text,
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
        "confidence": confidence,
    }


def test_sample_window_selects_the_most_subtitle_dense_thirty_seconds():
    cues = [
        {"start": 8, "end": 12, "text": "first"},
        {"start": 18, "end": 26, "text": "second"},
        {"start": 40, "end": 48, "text": "third"},
    ]

    start, end = subtitle_frame_qa.select_sample_window(cues, 80)

    assert start == 18
    assert end == 48


def test_sample_window_requires_at_least_thirty_seconds():
    try:
        subtitle_frame_qa.select_sample_window([], 29.9)
    except ValueError as error:
        assert "at least 30 seconds" in str(error)
    else:
        raise AssertionError("a short video must not be reported as a 30-second test")


def test_tesseract_tsv_words_are_grouped_into_positioned_lines():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\t"
        "width\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t100\t850\t60\t36\t90.0\tHello\n"
        "5\t1\t1\t1\t1\t2\t170\t850\t80\t36\t80.0\tworld\n"
    )

    lines = subtitle_frame_qa.parse_tesseract_tsv(tsv)

    assert lines == [
        {
            "text": "Hello world",
            "confidence": 85.0,
            "left": 100,
            "top": 850,
            "right": 250,
            "bottom": 886,
        }
    ]


def test_duplicate_visible_text_in_the_same_frame_fails():
    frames = [
        {
            "index": 0,
            "lines": [
                tesseract_line("A repeated subtitle"),
                tesseract_line("A repeated subtitle", top=900),
            ],
        }
    ]

    result = subtitle_frame_qa.analyze_frames(frames, 1920, 1080, [], 0)

    assert result["status"] == "failed"
    assert any(issue["code"] == "duplicate_visible_text" for issue in result["issues"])


def test_text_reappearing_on_adjacent_frames_is_not_a_duplicate():
    frames = [
        {"index": index, "lines": [tesseract_line("One subtitle")]}
        for index in range(4)
    ]

    result = subtitle_frame_qa.analyze_frames(frames, 1920, 1080, [], 0)

    assert result["summary"]["errors"] == 0
    assert not any(issue["code"] == "duplicate_visible_text" for issue in result["issues"])


def test_frame_qa_flags_text_that_is_too_small_or_near_the_screen_edge():
    frames = [
        {
            "index": 7,
            "timestamp_seconds": 0,
            "lines": [
                tesseract_line("Tiny", left=4, top=1000, width=50, height=8),
            ],
        }
    ]

    result = subtitle_frame_qa.analyze_frames(frames, 1920, 1080, [], 0)
    codes = {issue["code"] for issue in result["issues"]}

    assert "unsafe_screen_margin" in codes
    assert "text_too_small" in codes
    assert all(
        issue.get("timestamp_seconds") == 0
        for issue in result["issues"]
        if issue.get("frame") == 7
    )


def test_exact_repeated_cues_are_reported_as_warnings_not_duplicates():
    cues = [
        {"start": 2, "end": 4, "text": "Same line"},
        {"start": 4.5, "end": 6, "text": "same line"},
    ]
    frames = [{"index": 0, "lines": [tesseract_line("Same line")]}]

    result = subtitle_frame_qa.analyze_frames(frames, 1920, 1080, cues, 0)

    assert result["status"] == "passed_with_warnings"
    assert any(issue["code"] == "repeated_caption" for issue in result["issues"])


def test_cumulative_rolling_cues_report_the_repeated_prefix():
    cues = [
        {"start": 2, "end": 4, "text": "总统先生艾米阿特金斯的母亲"},
        {"start": 4.5, "end": 6, "text": "总统先生艾米阿特金斯的母亲后来受到威胁"},
    ]
    frames = [{"index": 0, "lines": [tesseract_line("总统先生")]}]

    result = subtitle_frame_qa.analyze_frames(frames, 1920, 1080, cues, 0)

    issue = next(
        item
        for item in result["issues"]
        if item["code"] == "rolling_caption_repeats_prefix"
    )
    assert issue["severity"] == "warning"
    assert issue["repeated_characters"] == 13


def test_empty_ocr_result_fails_instead_of_claiming_the_frames_passed():
    result = subtitle_frame_qa.analyze_frames(
        [{"index": 0, "lines": []}], 1920, 1080, [], 0
    )

    assert result["status"] == "failed"
    assert result["issues"][0]["code"] == "no_captions_detected"
