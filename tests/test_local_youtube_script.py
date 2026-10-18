import importlib.util
import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "download_youtube_bilingual_srt.py"
SPEC = importlib.util.spec_from_file_location("local_youtube_srt", SCRIPT)
youtube_srt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(youtube_srt)


def test_video_id_url_variants():
    assert youtube_srt.video_id_from_url("https://www.youtube.com/watch?v=IySF_8OArvw") == "IySF_8OArvw"
    assert youtube_srt.video_id_from_url("https://youtu.be/IySF_8OArvw") == "IySF_8OArvw"


def test_parse_and_merge_srt_chinese_above_english():
    en = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n")
    zh = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\n你好\n\n")
    result = youtube_srt.align_and_merge(zh, en)
    assert "00:00:01,000 --> 00:00:02,000\n你好\nHello" in result
    assert youtube_srt.validate_srt(result) == 1


def test_serialize_single_track_as_strict_srt():
    result = youtube_srt.serialize_track(
        [{"start": 1, "end": 2, "text": "<i>Hello</i>"}]
    )
    assert result == "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    assert youtube_srt.validate_srt(result) == 1


def test_language_preference_is_case_insensitive():
    assert youtube_srt.pick_language({"en": [], "en-orig": []}, ["en-orig", "en"]) == "en-orig"
    assert youtube_srt.pick_language({"zh-Hans": []}, ["zh-hans"]) == "zh-Hans"
    assert youtube_srt.classify_track(
        "zh-Hans", {"language": "en", "automatic_captions": {"zh-Hans": []}}
    ) == "auto-translated"
    assert youtube_srt.classify_track(
        "en-orig", {"language": "en", "automatic_captions": {"en-orig": []}}
    ) == "auto-generated"
    assert youtube_srt.subtitle_wait("en-orig", 56) == 0
    assert youtube_srt.subtitle_wait("zh-Hans", 56) == 56
    assert youtube_srt.subtitle_wait("zh-CN", 20) == 20


def test_parser_removes_markup_and_empty_caption_lines():
    parsed = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n<b>Hello</b>\n<i>world</i>\n\n"
    )
    assert parsed[0]["text"] == "Hello\nworld"


def test_parser_handles_blank_after_timestamp_without_shifting_cues():
    source = (
        "1\n00:00:01,000 --> 00:00:02,000\n\nHello\n\n"
        "2\n00:00:02,000 --> 00:00:02,010\n \n\n"
        "3\n00:00:02,010 --> 00:00:03,000\nHello again\n\n"
    )
    parsed = youtube_srt.parse_srt(source)
    assert [(cue["start"], cue["end"], cue["text"]) for cue in parsed] == [
        (1.0, 2.0, "Hello"),
        (2.01, 3.0, "Hello again"),
    ]


def test_strict_validator_rejects_styling_tags_and_bad_times():
    try:
        youtube_srt.validate_srt(
            '1\n00:00:02,000 --> 00:00:01,000\n<font color="#FFFFFF">Text</font>\n'
        )
    except ValueError as error:
        assert "invalid time range" in str(error)
    else:
        raise AssertionError("Expected non-positive subtitle time range to fail")


def test_strict_validator_rejects_nonstandard_markup():
    try:
        youtube_srt.validate_srt('1\n00:00:01,000 --> 00:00:02,000\n<b>Text</b>\n')
    except ValueError as error:
        assert "styling tags" in str(error)
    else:
        raise AssertionError("Expected styling markup to fail strict SRT validation")


def test_write_srt_emits_utf8_bom_and_crlf(tmp_path):
    path = tmp_path / "valid.srt"
    content = "1\n00:00:01,000 --> 00:00:02,000\n你好\nHello\n"
    youtube_srt.write_srt(path, content)
    raw = path.read_bytes()
    assert raw.startswith(bytes.fromhex("efbbbf"))
    assert raw.count(b"\r\n") > 0
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert youtube_srt.validate_srt(path.read_text(encoding="utf-8-sig")) == 1


def test_download_track_matches_yt_dlp_extended_language_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_srt.shutil, "which", lambda _: "yt-dlp.exe")

    def fake_run(_args, **_kwargs):
        assert _kwargs["capture_output"] is False
        assert "--sleep-subtitles" not in _args
        subtitle = tmp_path / "IySF_8OArvw.en.en-orig.srt"
        subtitle.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(youtube_srt.subprocess, "run", fake_run)
    entries = youtube_srt.download_track(
        "https://youtu.be/IySF_8OArvw",
        "IySF_8OArvw",
        "en-orig",
        "auto-generated",
        tmp_path,
        0,
    )
    assert entries[0]["text"] == "Hello"


def test_main_saves_both_individual_tracks_and_the_bilingual_srt(monkeypatch, tmp_path, capsys):
    downloaded_languages = []

    def fake_run_yt_dlp(arguments, **_kwargs):
        if "--dump-single-json" in arguments:
            metadata = {
                "id": "IySF_8OArvw",
                "title": "Example event",
                "upload_date": "20261018",
                "language": "en",
                "subtitles": {},
                "automatic_captions": {"en-orig": [], "zh-Hans": []},
            }
            return subprocess.CompletedProcess([], 0, json.dumps(metadata), "")

        language = arguments[arguments.index("--sub-langs") + 1]
        wait_index = arguments.index("--sleep-subtitles") if "--sleep-subtitles" in arguments else None
        wait = arguments[wait_index + 1] if wait_index is not None else "0"
        downloaded_languages.append((language, wait))
        template = Path(arguments[arguments.index("-o") + 1])
        subtitle_path = Path(
            str(template)
            .replace("%(id)s", "IySF_8OArvw")
            .replace("%(language)s", language)
            .replace("%(ext)s", "srt")
        )
        text = "Hello" if language == "en-orig" else "你好"
        subtitle_path.write_text(
            f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(youtube_srt, "run_ytdlp", fake_run_yt_dlp)
    assert youtube_srt.main(
        [
            "https://www.youtube.com/watch?v=IySF_8OArvw",
            "--output-dir",
            str(tmp_path),
        ]
    ) == 0

    output_lines = [
        line for line in capsys.readouterr().out.splitlines() if line.endswith(".srt")
    ]
    assert len(output_lines) == 3
    chinese = Path(output_lines[0])
    english = Path(output_lines[1])
    bilingual = Path(output_lines[2])
    assert chinese.name == "auto-translated_zh-Hans.srt"
    assert english.name == "auto-generated_en-orig.srt"
    assert bilingual.name == (
        "auto-translated+auto-generated_Bilingual_zh-Hans-en-orig.srt"
    )
    assert "Hello" in english.read_text(encoding="utf-8-sig")
    assert "你好" in chinese.read_text(encoding="utf-8-sig")
    assert "你好\nHello" in bilingual.read_text(encoding="utf-8-sig")
    assert downloaded_languages == [("en-orig", "0"), ("zh-Hans", "56")]
