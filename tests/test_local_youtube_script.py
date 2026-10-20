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


def test_ass_uses_chinese_above_english_with_chinese_1_2x_size():
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n你好\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)

    assert "PlayResX: 1920" in result
    assert "PlayResY: 1080" in result
    assert "Style: Chinese,Microsoft YaHei,57.6," in result
    assert "Style: English,Arial,48," in result
    assert "BorderStyle, Outline, Shadow" in result
    assert "Style: Chinese,Microsoft YaHei,57.6,&H0099ECFF," in result
    assert ",1,1,1,5,0,0,0,1" in result
    assert result.count("Dialogue: ") == 1
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Chinese" in result
    assert (
        r"{\an5\pos(959,952.5)}{\fs57.6}你好\N{\fs10}\h\N"
        r"{\rEnglish\fs48}Hello"
    ) in result
    assert result.count("MarginV") == 2


def test_ass_caption_area_can_be_scaled_by_the_renderer_on_larger_playres():
    cues = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\ntext\n\n")

    result = youtube_srt.build_ass(
        cues,
        cues,
        caption_area=(132, 1766, 3704, 2044),
        play_res_x=3840,
        play_res_y=2160,
    )

    assert "PlayResX: 3840" in result
    assert "PlayResY: 2160" in result
    assert r"\pos(1918,1905)" in result


def test_ass_styles_support_custom_text_colors():
    cues = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\ncaption\n\n")
    result = youtube_srt.build_ass(
        cues,
        cues,
        first_color="#FF0000",
        second_color="#0000FF",
    )

    assert "Style: Chinese,Microsoft YaHei,57.6,&H000000FF," in result
    assert "Style: English,Arial,48,&H00FF0000," in result


def test_ass_classifies_mixed_lines_and_centers_each_language():
    top = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:03,000\nHello there\n你好\n世界\n\n"
    )
    bottom = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:03,000\nGood morning\nこんにちは\n\n"
    )

    result = youtube_srt.build_ass(top, bottom)

    assert "你好世界こんにちは" in result
    assert r"\N{\fs10}\h\N{\rEnglish\fs48}Hello there Good morning" in result
    assert result.count("Dialogue: ") == 1


def test_long_ass_lines_shrink_to_fit_caption_box():
    english_text = "A long English caption " * 100
    chinese = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\n你好\n\n")
    english = youtube_srt.parse_srt(
        f"1\n00:00:01,000 --> 00:00:02,000\n{english_text}\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)

    english_style = result.split(r"\rEnglish", 1)[1].split("}", 1)[0]
    assert english_style.startswith(r"\fs")
    assert english_style != r"\fs48"


def test_ass_converts_caption_markup_and_escapes_literal_override_braces():
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n<b>你好</b> {\\pos(1,2)}\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n<i>Hello &amp; bye</i>\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)

    assert r"{\b1}你好{\b0}" in result
    assert r"\{\\pos(1,2)\}" in result
    assert r"{\i1}Hello & bye{\i0}" in result


def test_ass_rejects_invalid_font_parameters():
    cues = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\ntext\n\n")
    try:
        youtube_srt.build_ass(cues, cues, first_font="font,override")
    except ValueError as error:
        assert "font name" in str(error)
    else:
        raise AssertionError("Expected an invalid ASS font name to fail")


def test_burn_in_uses_ass_filter_and_copies_audio(monkeypatch, tmp_path):
    calls = []
    video = tmp_path / "source.mp4"
    ass = tmp_path / "captions.ass"
    output = tmp_path / "burned.mp4"
    video.touch()
    ass.touch()
    monkeypatch.setattr(youtube_srt.shutil, "which", lambda _: "ffmpeg.exe")
    monkeypatch.setattr(
        youtube_srt.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    youtube_srt.burn_ass_into_video(video, ass, output)

    command, options = calls[0]
    escaped_ass = ass.as_posix().replace(":", r"\:")
    vf = command[command.index("-vf") + 1]
    assert vf.startswith("drawbox=x=iw*66/1920:y=ih*883/1080:w=iw*1786/1920:h=ih*139/1080:")
    assert vf.endswith(f",subtitles=filename='{escaped_ass}'")
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-crf") + 1] == "18"
    assert "-map" not in command
    assert options["check"] is True


def test_qsv_burn_in_uses_requested_one_line_ffmpeg_quality_options(monkeypatch, tmp_path):
    calls = []
    video, ass, output = (tmp_path / name for name in ("source.mp4", "captions.ass", "burned.mp4"))
    video.touch()
    ass.touch()
    monkeypatch.setattr(youtube_srt.shutil, "which", lambda _: "ffmpeg.exe")
    monkeypatch.setattr(
        youtube_srt.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    youtube_srt.burn_ass_into_video(
        video, ass, output, video_encoder="h264_qsv", global_quality=1
    )

    command = calls[0]
    assert command[command.index("-c:v") + 1] == "h264_qsv"
    assert command[command.index("-global_quality") + 1] == "1"
    assert command[command.index("-c:a") + 1] == "copy"
    assert "color=0x000000@0.6:t=fill" in command[command.index("-vf") + 1]


def test_drawbox_opacity_and_color_are_validated(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_srt.shutil, "which", lambda _: "ffmpeg.exe")
    video, ass, output = (tmp_path / name for name in ("source.mp4", "captions.ass", "burned.mp4"))
    video.touch()
    ass.touch()
    for options in ({"box_opacity": 1.1}, {"box_color": "not-a-color"}):
        try:
            youtube_srt.burn_ass_into_video(video, ass, output, **options)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected invalid drawbox configuration to fail")


def test_ytdlp_uses_deno_runtime_when_available(monkeypatch):
    captured = []

    def fake_which(name):
        return f"{name}.exe"

    monkeypatch.setattr(youtube_srt.shutil, "which", fake_which)
    monkeypatch.setattr(
        youtube_srt.subprocess,
        "run",
        lambda command, **kwargs: captured.append((command, kwargs))
        or subprocess.CompletedProcess(command, 0, "{}", ""),
    )

    youtube_srt.run_ytdlp(["--dump-single-json", "https://youtu.be/IySF_8OArvw"])

    assert captured[0][0][:3] == ["yt-dlp.exe", "--js-runtimes", "deno"]


def test_merge_preserves_source_caption_text_verbatim():
    chinese = youtube_srt.parse_srt(
        "4\n00:00:01,230 --> 00:00:02,340\n  <b>Source &amp; markup</b>  \n\n"
    )
    english = youtube_srt.parse_srt(
        "8\n00:00:01,000 --> 00:00:03,000\n <i>Source &amp; markup</i> \n\n"
    )
    merged = youtube_srt.align_and_merge(chinese, english)
    assert merged == (
        "1\n00:00:01,000 --> 00:00:01,230\n"
        " <i>Source &amp; markup</i> \n\n"
        "2\n00:00:01,230 --> 00:00:02,340\n"
        "  <b>Source &amp; markup</b>  \n"
        " <i>Source &amp; markup</i> \n\n"
        "3\n00:00:02,340 --> 00:00:03,000\n"
        " <i>Source &amp; markup</i> \n"
    )


def test_merge_aligns_only_overlapping_cues():
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:03,000\n中文原句\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:00:02,000 --> 00:00:04,000\nEnglish source\n\n"
    )

    merged = youtube_srt.align_and_merge(chinese, english)

    assert merged == (
        "1\n00:00:01,000 --> 00:00:02,000\n中文原句\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n中文原句\nEnglish source\n\n"
        "3\n00:00:03,000 --> 00:00:04,000\nEnglish source\n"
    )
    assert youtube_srt.validate_srt(merged) == 3


def test_merge_keeps_rolling_cues_in_source_order_in_one_block():
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,840 --> 00:00:06,840\n介绍\n\n"
        "2\n00:00:03,480 --> 00:00:10,360\n身份\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:00:01,840 --> 00:00:06,840\nLadies and gentlemen\n\n"
        "2\n00:00:03,480 --> 00:00:10,360\nthe United States\n\n"
    )

    merged = youtube_srt.align_and_merge(chinese, english)

    assert (
        "00:00:03,480 --> 00:00:06,840\n"
        "介绍\n身份\n"
        "Ladies and gentlemen\nthe United States"
    ) in merged
    assert merged.index("Ladies and gentlemen") < merged.index("the United States")
    assert youtube_srt.validate_srt(merged) == 3


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


def test_subtitle_wait_only_applies_to_chinese_tracks():
    assert youtube_srt.subtitle_wait("en", 56) == 0
    assert youtube_srt.subtitle_wait("en-orig", 56) == 0
    assert youtube_srt.subtitle_wait("zh-Hans", 56) == 56
    assert youtube_srt.subtitle_wait("zh-Hant", 30) == 30


def test_parser_preserves_original_markup_and_caption_text():
    parsed = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n<b>Hello</b>\n<i> world </i>\n\n"
    )
    assert parsed[0]["text"] == "<b>Hello</b>\n<i> world </i>"


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


def test_validator_allows_original_caption_markup_in_merged_srt():
    assert youtube_srt.validate_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n<b>Text</b>\n<i>翻译</i>\n"
    ) == 1


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
    source_path, entries = entries
    assert source_path.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
    )
    assert entries[0]["text"] == "Hello"


def test_main_preserves_raw_tracks_and_merges_original_caption_content(monkeypatch, tmp_path, capsys):
    downloaded_languages = []
    original_bytes = {}

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
        text = "<i>Hello &amp; welcome</i>" if language == "en-orig" else "<b>你好</b>"
        source_content = (
            f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n\n"
        )
        subtitle_path.write_text(source_content, encoding="utf-8")
        original_bytes[language] = subtitle_path.read_bytes()
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(youtube_srt, "run_ytdlp", fake_run_yt_dlp)
    assert youtube_srt.main(
        [
            "https://www.youtube.com/watch?v=IySF_8OArvw",
            "--output-dir",
            str(tmp_path),
        ]
    ) == 0

    output_lines = capsys.readouterr().out.splitlines()
    source_paths = [
        Path(line)
        for line in output_lines
        if line.startswith(str(tmp_path)) and "IySF_8OArvw." in line
    ]
    bilingual_paths = [
        Path(line)
        for line in output_lines
        if line.startswith(str(tmp_path)) and "_Bilingual_" in line and line.endswith(".srt")
    ]
    ass_paths = [
        Path(line)
        for line in output_lines
        if line.startswith(str(tmp_path)) and "_Bilingual_" in line and line.endswith(".ass")
    ]
    assert len(source_paths) == 2
    assert len(bilingual_paths) == 1
    assert len(ass_paths) == 1
    raw_files = {path.name.split(".")[-2]: path for path in source_paths}
    assert raw_files["en-orig"].read_bytes() == original_bytes["en-orig"]
    assert raw_files["zh-Hans"].read_bytes() == original_bytes["zh-Hans"]
    bilingual = bilingual_paths[0].read_text(encoding="utf-8-sig")
    assert "<b>你好</b>\n<i>Hello &amp; welcome</i>" in bilingual
    assert "&amp;" in bilingual
    assert "<i>" in bilingual
    assert downloaded_languages == [("en-orig", "0"), ("zh-Hans", "56")]
    assert "Style: Chinese,Microsoft YaHei,57.6," in ass_paths[0].read_text(encoding="utf-8-sig")
