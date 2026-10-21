import importlib.util
import json
import re
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "download_youtube_bilingual_srt.py"
SPEC = importlib.util.spec_from_file_location("local_youtube_srt", SCRIPT)
youtube_srt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(youtube_srt)


def without_karaoke_tags(value):
    return re.sub(r"\{\\kt\d+\\k\d+\}", "", value)


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
    assert "Style: Chinese,Microsoft YaHei,57.6,&H0028C9FF," in result
    assert "Style: CaptionBox,Arial,1,&H66000000," in result
    assert ",1,2,1,5,0,0,0,1" in result
    assert result.count("Dialogue: ") == 3
    assert "Dialogue: 1,0:00:01.00,0:00:02.00,Chinese" in result
    assert "Style: CaptionFacet,Arial,1,&HD9FFFFFF," in result
    assert "Dialogue: 0,0:00:00.00,0:00:02.00,CaptionFacet" in result
    assert (
        "Dialogue: 0,0:00:00.00,0:00:02.00,CaptionBox,,0,0,0,,"
        r"{\an7\pos(66,883)\p1}m 0 0 l 1786 0 1786 139 0 139 0 0{\p0}"
    ) in result
    assert (
        r"{\an5\pos(959,952.5)}{\fs57.6}你好\N{\fs10}\h\N"
        r"{\rEnglish\fs48}{\kt0\k100}Hello"
    ) in result
    assert "Style: English,Arial,48,&H00FFD700,&H00FFFFFF," in result


def test_ass_highlights_english_words_in_sequence():
    chinese = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:03,000\n你好\n\n")
    english = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:03,000\nHello world!\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)

    assert r"{\rEnglish\fs48}{\kt0\k100}Hello {\kt100\k100}world!" in result
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


def test_ass_caption_box_covers_video_from_zero_through_duration():
    cues = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\ncaption\n\n")

    result = youtube_srt.build_ass(cues, cues, video_duration=12.5)

    assert result.count("Style: CaptionBox,") == 1
    assert (
        "Dialogue: 0,0:00:00.00,0:00:12.50,CaptionBox,,0,0,0,,"
    ) in result


def test_box_can_appear_only_during_caption_intervals():
    top = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\nFirst\n\n"
        "2\n00:00:04,000 --> 00:00:05,000\nSecond\n\n"
    )
    bottom = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\n第一\n\n"
        "2\n00:00:04,000 --> 00:00:05,000\n第二\n\n"
    )

    result = youtube_srt.build_ass(top, bottom, box_display="with-captions")

    assert "Dialogue: 0,0:00:00.00," not in result
    box_lines = [line for line in result.splitlines() if ",CaptionBox,," in line]
    assert len(box_lines) == 2
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,CaptionBox" in box_lines[0]
    assert "Dialogue: 0,0:00:04.00,0:00:05.00,CaptionBox" in box_lines[1]


def test_box_styles_apply_default_colors_and_opacity():
    cues = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\ntext\n\n")
    glass = youtube_srt.build_ass(
        cues,
        cues,
        box_style="glass-diamond",
        box_color=youtube_srt.BOX_STYLES["glass-diamond"]["color"],
        box_opacity=youtube_srt.BOX_STYLES["glass-diamond"]["opacity"],
    )
    blue = youtube_srt.build_ass(
        cues,
        cues,
        box_style="presidential-blue",
        box_color=youtube_srt.BOX_STYLES["presidential-blue"]["color"],
        box_opacity=youtube_srt.BOX_STYLES["presidential-blue"]["opacity"],
    )
    solid = youtube_srt.build_ass(
        cues,
        cues,
        box_style="solid-color",
        box_color="#F8F9FA",
        box_opacity=1,
    )

    assert "Style: CaptionBox,Arial,1,&H52968470," in glass
    assert "Style: CaptionFacet,Arial,1,&HD9FFFFFF," in glass
    assert "Style: CaptionBox,Arial,1,&H1A47160F," in blue
    assert ",CaptionFacet,," not in blue
    assert "Style: CaptionBox,Arial,1,&H00FAF9F8," in solid
    assert ",CaptionFacet,," not in solid


def test_cinema_preset_places_english_above_chinese_and_supports_custom_box():
    cues = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:02,000\nPrimary school\n\n"
    )
    chinese = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\n小学生\n\n")

    result = youtube_srt.build_ass(
        cues,
        chinese,
        first_font="SimSun",
        second_font="Georgia",
        first_color="#FFF0D0",
        second_color="#FFF0D0",
        caption_area=(100, 800, 1800, 1000),
        caption_order="english-first",
        video_duration=8,
    )

    assert "Style: Chinese,SimSun,57.6,&H00D0F0FF," in result
    assert "Style: English,Georgia,48,&H00FFD700,&H00D0F0FF," in result
    assert (
        "Dialogue: 0,0:00:00.00,0:00:08.00,CaptionBox,,0,0,0,,"
        r"{\an7\pos(100,800)\p1}m 0 0 l 1700 0 1700 200 0 200 0 0{\p0}"
    ) in result
    assert (
        r"{\an5\pos(950,900)}{\rEnglish\fs48}{\kt0\k50}Primary "
        r"{\kt50\k50}school"
        r"\N{\fs10}\h\N{\rChinese\fs57.6}小学生"
    ) in result


def test_ass_styles_support_custom_text_colors():
    cues = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\ncaption\n\n")
    result = youtube_srt.build_ass(
        cues,
        cues,
        first_color="#FF0000",
        second_color="#0000FF",
    )

    assert "Style: Chinese,Microsoft YaHei,57.6,&H000000FF," in result
    assert "Style: English,Arial,48,&H00FFD700,&H00FF0000," in result


def test_ass_classifies_mixed_lines_and_centers_each_language():
    top = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:03,000\nHello there\n你好\n世界\n\n"
    )
    bottom = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:03,000\nGood morning\nこんにちは\n\n"
    )

    result = youtube_srt.build_ass(top, bottom)

    assert "你好世界こんにちは" in result
    assert r"\N{\fs10}\h\N{\rEnglish\fs48}" in result
    assert "Hello there Good morning" in without_karaoke_tags(result)
    assert result.count("Dialogue: ") == 3


def test_ass_flattens_source_wrapped_lines_into_one_line_per_language():
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:03,000\n小学生要好好\n学习\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:03,000\nPrimary school students\n"
        "should study hard.\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)

    assert "小学生要好好学习" in result
    assert "Primary school students should study hard." in without_karaoke_tags(result)
    assert "小学生要好好\\N学习" not in result


def test_ass_reconstructs_overlapping_rolling_captions_as_single_lines():
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:04,000\n今天下午，工作组\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\n今天下午，工作组将召开会议\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:04,000\nThis afternoon, the task force\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\nThis afternoon, the task force will meet\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)

    assert "今天" in result
    assert "将召开会议" in result
    assert "This afternoon" in without_karaoke_tags(result)
    assert "the task force" in without_karaoke_tags(result)
    assert "今天下午，工作组将召开会议" not in result
    assert "This afternoon, the task force will meet" not in result
    assert "will meet" in without_karaoke_tags(result)
    assert "Dialogue: 1,0:00:01.00,0:00:02.00" in result
    assert "Dialogue: 1,0:00:02.00,0:00:05.00" in result


def test_ass_serializes_rolling_cues_with_different_end_times_without_repeats():
    cues = youtube_srt.parse_srt(
        "1\n00:01:05,760 --> 00:01:08,960\nGood morning. Welcome\n\n"
        "2\n00:01:08,960 --> 00:01:13,200\n"
        "Good morning. Welcome cabinet members, doctors, directors,\n\n"
        "3\n00:01:13,200 --> 00:01:14,720\n"
        "cabinet members, doctors, directors, administrators,\n\n"
        "4\n00:01:14,720 --> 00:01:18,000\n"
        "administrators, and all in attendance today.\n\n"
    )

    serialized = youtube_srt._serialize_same_end_cues(cues)

    assert [(cue["start"], cue["end"]) for cue in serialized] == [
        (65.76, 68.96),
        (68.96, 73.2),
        (73.2, 74.72),
        (74.72, 78.0),
    ]
    assert " ".join(cue["text"] for cue in serialized) == (
        "Good morning. Welcome cabinet members, doctors, directors, "
        "administrators, and all in attendance today."
    )


def test_ass_rolls_overlapping_youtube_caption_window_into_one_line():
    chinese = youtube_srt.parse_srt(
        "1\n00:00:04,160 --> 00:00:08,720\n女士们先生们，美国总统\n\n"
        "2\n00:00:06,160 --> 00:00:13,000\n唐纳德·特朗普和\n\n"
        "3\n00:00:08,720 --> 00:00:13,000\n第一夫人梅拉尼娅·特朗普\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:00:04,160 --> 00:00:08,720\nLadies and gentlemen, the President of\n\n"
        "2\n00:00:06,160 --> 00:00:13,000\nthe United States, Donald J. Trump, and\n\n"
        "3\n00:00:08,720 --> 00:00:13,000\nFirst Lady Melania Trump.\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)

    events = {
        line.split(",", 3)[1]: line
        for line in result.splitlines()
        if line.startswith("Dialogue: 1,")
    }
    assert "0:00:04.16" in events
    assert "0:00:06.16" not in events
    assert "0:00:08.72" in events
    assert "Dialogue: 1,0:00:04.16,0:00:08.72" in events["0:00:04.16"]
    assert (
        "Ladies and gentlemen, the President of the United States, "
        "Donald J. Trump, and"
    ) in without_karaoke_tags(events["0:00:04.16"])
    assert "女士们先生们，美国总统唐纳德·特朗普和" in events["0:00:04.16"]
    assert "Dialogue: 1,0:00:08.72,0:00:13.00" in events["0:00:08.72"]
    assert "Ladies and gentlemen" not in events["0:00:08.72"]
    assert "First Lady Melania Trump." in without_karaoke_tags(events["0:00:08.72"])
    assert "第一夫人梅拉尼娅·特朗普" in events["0:00:08.72"]
    assert r"特\N{\fs10}" not in events["0:00:08.72"]
    assert (
        r"\N{\fs10}\h\N{\rEnglish\fs48}First Lady Melania Trump."
        in without_karaoke_tags(events["0:00:08.72"])
    )


def test_ass_serializes_three_same_end_cues_without_gaps_or_overlap():
    cues = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:05,000\nWe\n\n"
        "2\n00:00:02,000 --> 00:00:05,000\nare making\n\n"
        "3\n00:00:03,000 --> 00:00:05,000\nsteady progress now\n\n"
    )

    serialized = youtube_srt._serialize_same_end_cues(cues)

    assert [(cue["start"], cue["end"]) for cue in serialized] == [
        (1.0, 2.0),
        (2.0, 3.0),
        (3.0, 5.0),
    ]
    assert " ".join(cue["text"] for cue in serialized) == (
        "We are making steady progress now"
    )


def test_ass_word_rate_counts_name_initials_as_one_word():
    text = "the United States, Donald J. Trump, and First Lady Melania Trump."

    assert len(youtube_srt._rolling_word_spans(text)) == 10


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
    assert r"{\i1}Hello & bye{\i0}" in without_karaoke_tags(result)


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
    assert vf == f"subtitles=filename='{escaped_ass}'"
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


def test_ass_caption_box_is_embedded_and_opacity_is_validated():
    cues = youtube_srt.parse_srt("1\n00:00:01,000 --> 00:00:02,000\ncaption\n\n")
    result = youtube_srt.build_ass(cues, cues)
    assert "Style: CaptionBox,Arial,1,&H66000000," in result

    try:
        youtube_srt.build_ass(cues, cues, box_opacity=1.1)
    except ValueError as error:
        assert "Box opacity" in str(error)
    else:
        raise AssertionError("Expected invalid ASS box opacity to fail")


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


def test_merge_uses_one_latest_caption_per_language_without_rolling():
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,840 --> 00:00:06,840\n介绍\n\n"
        "2\n00:00:03,480 --> 00:00:10,360\n身份\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:00:01,840 --> 00:00:06,840\nLadies and gentlemen\n\n"
        "2\n00:00:03,480 --> 00:00:10,360\nthe United States\n\n"
    )

    merged = youtube_srt.align_and_merge(chinese, english)

    assert merged == (
        "1\n00:00:01,840 --> 00:00:03,480\n"
        "介绍\nLadies and gentlemen\n\n"
        "2\n00:00:03,480 --> 00:00:10,360\n"
        "身份\nthe United States\n"
    )
    assert "介绍" not in merged.split("2\n", 1)[1]
    assert youtube_srt.validate_srt(merged) == 2

    ass = youtube_srt.build_ass(chinese, english)
    assert "介绍" in ass
    assert "身份" in ass
    assert r"介绍\N身份" not in ass
    assert r"Ladies and gentlemen the United States" in without_karaoke_tags(ass)


def test_merge_pairs_cues_with_the_same_end_time_into_two_lines():
    chinese = youtube_srt.parse_srt(
        "1\n00:01:18,000 --> 00:01:25,320\n支持\n这一重要\n\n"
    )
    english = youtube_srt.parse_srt(
        "1\n00:01:20,560 --> 00:01:25,320\nsupports\nthis key\n\n"
    )

    merged = youtube_srt.align_and_merge(chinese, english)

    assert merged == (
        "1\n00:01:18,000 --> 00:01:25,320\n"
        "支持这一重要\nsupports this key\n"
    )


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


def test_vtt_parser_extracts_cues_and_inline_word_timestamps():
    parsed = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "cue-1\n"
        "00:00:01.000 --> 00:00:03.000 align:start position:0%\n"
        '<v Speaker><00:00:01.200>Hello <00:00:01.800>world!</v>\n\n'
        "00:03.000 --> 00:04.000\n"
        "<c.green>Good morning.</c>\n"
    )

    assert len(parsed) == 2
    assert (parsed[0]["start"], parsed[0]["end"]) == (1.0, 3.0)
    assert parsed[0]["text"] == "Hello world!"
    assert [(word["start"], word["text"]) for word in parsed[0]["word_timings"]] == [
        (1.2, "Hello"),
        (1.8, "world"),
    ]
    assert parsed[1]["text"] == "Good morning."
    assert "word_timings" not in parsed[1]


def test_vtt_parser_skips_blank_line_after_timing_header():
    parsed = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "00:00:03.960 --> 00:00:06.030 align:start position:0%\n"
        "\n"
        "Ladies<00:00:04.440> and gentlemen,\n\n"
        "00:00:06.030 --> 00:00:06.040 align:start position:0%\n"
        "Ladies and gentlemen,\n"
    )

    assert [(cue["start"], cue["end"]) for cue in parsed] == [
        (3.96, 6.03),
        (6.03, 6.04),
    ]
    assert parsed[0]["text"] == "Ladies and gentlemen,"


def test_ass_uses_vtt_word_timestamps_instead_of_uniform_estimate():
    cues = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "<00:00:01.100>Ladies <00:00:01.400>and "
        "<00:00:01.800>gentlemen, <00:00:02.200>the President\n\n"
        "00:00:02.000 --> 00:00:03.000\n"
        "<00:00:02.000>the President <00:00:02.400>of the United States\n"
    )

    serialized = youtube_srt._serialize_same_end_cues(cues)

    assert [(cue["start"], cue["end"], cue["text"]) for cue in serialized] == [
        (1.0, 2.0, "Ladies and gentlemen,"),
        (2.0, 3.0, "the President of the United States"),
    ]


def test_ass_word_highlights_follow_vtt_pronunciation_timestamps():
    english = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "<00:00:01.200>Hello <00:00:01.800>world!\n"
    )
    chinese = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\n你好\n"
    )

    result = youtube_srt.build_ass(chinese, english)
    english_line = next(
        line for line in result.splitlines() if line.startswith("Dialogue: 1,")
    )

    assert english_line.startswith("Dialogue: 1,0:00:01.00,0:00:03.00,")
    assert r"{\kt20\k60}Hello {\kt80\k120}world!" in english_line
    assert r"\kf" not in english_line


def test_ass_keeps_word_timing_when_serializing_rolling_vtt_cues():
    cues = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "<00:00:01.100>Hello <00:00:01.500>there\n\n"
        "00:00:02.000 --> 00:00:03.000\n"
        "<00:00:02.100>Hello there <00:00:02.500>today\n"
    )

    serialized = youtube_srt._serialize_same_end_cues(cues)
    result = youtube_srt.build_ass(
        youtube_srt.parse_vtt(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n你好\n"
        ),
        cues,
    )
    english_line = next(
        line for line in result.splitlines() if line.startswith("Dialogue: 1,")
    )

    assert [cue["text"] for cue in serialized] == ["Hello there", "today"]
    assert [timing["start"] for timing in serialized[0]["word_timings"]] == [1.1, 1.5]
    assert [timing["start"] for timing in serialized[1]["word_timings"]] == [2.5]
    assert r"{\kt10\k40}Hello {\kt50\k50}there" in english_line
    assert r"{\kt50\k50}today" in result


def test_ass_does_not_advance_cues_across_a_real_caption_gap():
    english = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:01,500\nFirst\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nSecond\n\n"
    )
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:01,500\n第一句\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n第二句\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)
    dialogues = [
        line for line in result.splitlines() if line.startswith("Dialogue: 1,")
    ]

    assert dialogues[0].startswith("Dialogue: 1,0:00:01.00,0:00:01.50,")
    assert dialogues[1].startswith("Dialogue: 1,0:00:02.00,0:00:03.00,")


def test_ass_preserves_a_short_gap_between_adjacent_caption_events():
    english = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:01,500\nFirst\n\n"
        "2\n00:00:01,600 --> 00:00:03,000\nSecond\n\n"
    )
    chinese = youtube_srt.parse_srt(
        "1\n00:00:01,000 --> 00:00:01,500\n第一句\n\n"
        "2\n00:00:01,600 --> 00:00:03,000\n第二句\n\n"
    )

    result = youtube_srt.build_ass(chinese, english)
    dialogues = [
        line for line in result.splitlines() if line.startswith("Dialogue: 1,")
    ]

    assert dialogues[0].startswith("Dialogue: 1,0:00:01.00,0:00:01.50,")
    assert dialogues[1].startswith("Dialogue: 1,0:00:01.60,0:00:03.00,")


def test_ass_skips_later_splits_after_vtt_timings_assign_all_words():
    cues = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:05.000\n"
        "<00:00:01.100>Hello <00:00:01.200>there <00:00:01.300>today\n\n"
        "00:00:02.000 --> 00:00:05.000\n"
        "Hello there\n\n"
        "00:00:03.000 --> 00:00:05.000\n"
        "Hello\n"
    )

    serialized = youtube_srt._serialize_same_end_cues(cues)

    assert serialized == [
        {
            **cues[0],
            "start": 1.0,
            "end": 2.0,
            "text": "Hello there today",
        }
    ]


def test_ass_merges_tiny_paired_rolling_fragment_into_visible_interval():
    cues = youtube_srt.parse_srt(
        "1\n00:00:06,030 --> 00:00:08,630\n"
        "Ladies and gentlemen, the president of\n\n"
        "2\n00:00:06,040 --> 00:00:08,630\n"
        "Ladies and gentlemen, the president of the United States\n"
    )
    signature = youtube_srt._cue_group_signature(cues)

    serialized = youtube_srt._serialize_same_end_cues(
        cues,
        paired_group_signatures={signature},
    )

    assert [(cue["start"], cue["end"]) for cue in serialized] == [(6.03, 8.63)]
    assert serialized[0]["text"] == (
        "Ladies and gentlemen, the president of the United States"
    )


def test_ass_preserves_vtt_start_and_trims_stale_inline_timed_tail():
    english = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "00:00:03.960 --> 00:00:06.030 align:start position:0%\n\n"
        "Ladies<00:00:04.440> and<00:00:04.560> gentlemen,"
        "<00:00:05.200> the<00:00:05.320> president"
        "<00:00:05.920> of\n\n"
        "00:00:06.030 --> 00:00:06.040 align:start position:0%\n"
        "Ladies and gentlemen, the president of\n\n"
        "00:00:06.040 --> 00:00:08.630 align:start position:0%\n"
        "Ladies and gentlemen, the president of the"
        "<00:00:06.120> United<00:00:06.560> States,"
        "<00:00:07.120> Donald<00:00:07.600> J."
        "<00:00:07.840> Trump,<00:00:08.480> and\n\n"
        "00:00:08.630 --> 00:00:08.640 align:start position:0%\n"
        "the United States, Donald J. Trump, and\n\n"
        "00:00:08.640 --> 00:01:05.630 align:start position:0%\n\n"
        "the United States, Donald J. Trump, and\n"
        "First<00:00:08.960> Lady<00:00:09.280> Melania"
        "<00:00:09.800> Trump.\n"
    )
    chinese = youtube_srt.parse_vtt(
        "WEBVTT\n\n"
        "00:00:03.960 --> 00:00:06.030 align:start position:0%\n\n"
        "女士们<00:00:04.613>、<00:00:05.266>先生们<00:00:05.919>，\n\n"
        "00:00:06.030 --> 00:00:06.040 align:start position:0%\n"
        "女士们、先生们，\n\n"
        "00:00:06.040 --> 00:00:08.630 align:start position:0%\n"
        "女士们、先生们，"
        "美国<00:00:06.528>总统<00:00:07.016>唐纳德"
        "<00:00:07.504>·J·<00:00:07.992>特朗普<00:00:08.480>和\n\n"
        "00:00:08.630 --> 00:00:08.640 align:start position:0%\n"
        "美国总统唐纳德·J·特朗普和\n\n"
        "00:00:08.640 --> 00:01:05.630 align:start position:0%\n\n"
        "美国总统唐纳德·J·特朗普和\n"
        "第一<00:00:08.872>夫人<00:00:09.104>梅拉尼娅"
        "<00:00:09.336>·<00:00:09.568>特朗普<00:00:09.800>。\n"
    )

    ass = youtube_srt.build_ass(chinese, english)
    dialogues = [
        line for line in ass.splitlines() if line.startswith("Dialogue: 1,")
    ]
    first_caption = next(
        line for line in dialogues
        if "Ladies and gentlemen" in without_karaoke_tags(line)
    )
    last_caption = next(
        line for line in dialogues
        if "First Lady Melania Trump." in without_karaoke_tags(line)
    )

    assert first_caption.startswith("Dialogue: 1,0:00:03.96,")
    assert ",0:01:05.63," not in last_caption
    assert last_caption.startswith("Dialogue: 1,0:00:08.64,0:00:10.30,")
    assert not any(
        line.startswith("Dialogue: 1,0:00:10.22,0:00:10.30,")
        for line in dialogues
    )


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


def test_write_vtt_converts_bilingual_srt_and_validates_output(tmp_path):
    path = tmp_path / "bilingual.vtt"

    youtube_srt.write_vtt(
        path,
        "1\n00:00:01,250 --> 00:00:02,500\n你好\nHello\n",
    )

    assert path.read_text(encoding="utf-8") == (
        "WEBVTT\n\n1\n00:00:01.250 --> 00:00:02.500\n你好\nHello\n"
    )
    parsed = youtube_srt.parse_vtt(path.read_text(encoding="utf-8"))
    assert [(cue["start"], cue["end"], cue["text"]) for cue in parsed] == [
        (1.25, 2.5, "你好\nHello")
    ]


def test_download_track_matches_yt_dlp_extended_language_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_srt.shutil, "which", lambda _: "yt-dlp.exe")

    def fake_run(_args, **_kwargs):
        assert _kwargs["capture_output"] is False
        assert "--sleep-subtitles" not in _args
        assert _args[_args.index("--sub-format") + 1] == "vtt/best"
        assert _args[_args.index("--convert-subs") + 1] == "vtt"
        subtitle = tmp_path / "IySF_8OArvw.en.en-orig.vtt"
        subtitle.write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n",
            encoding="utf-8",
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
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"
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
            .replace("%(ext)s", "vtt")
        )
        text = "<i>Hello &amp; welcome</i>" if language == "en-orig" else "<b>你好</b>"
        source_content = f"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n{text}\n"
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
        if line.startswith(str(tmp_path)) and "IySF_8OArvw." in line and line.endswith(".vtt")
    ]
    bilingual_paths = [
        Path(line)
        for line in output_lines
        if line.startswith(str(tmp_path)) and "_Bilingual_" in line and line.endswith(".srt")
    ]
    bilingual_vtt_paths = [
        Path(line)
        for line in output_lines
        if line.startswith(str(tmp_path)) and "_Bilingual_" in line and line.endswith(".vtt")
    ]
    ass_paths = [
        Path(line)
        for line in output_lines
        if line.startswith(str(tmp_path)) and "_Bilingual_" in line and line.endswith(".ass")
    ]
    assert len(source_paths) == 2
    assert len(bilingual_paths) == 1
    assert len(bilingual_vtt_paths) == 1
    assert len(ass_paths) == 1
    raw_files = {path.name.split(".")[-2]: path for path in source_paths}
    assert raw_files["en-orig"].read_bytes() == original_bytes["en-orig"]
    assert raw_files["zh-Hans"].read_bytes() == original_bytes["zh-Hans"]
    bilingual = bilingual_paths[0].read_text(encoding="utf-8-sig")
    assert "<b>你好</b>\n<i>Hello & welcome</i>" in bilingual
    assert "&amp;" not in bilingual
    assert "<i>" in bilingual
    bilingual_vtt = bilingual_vtt_paths[0].read_text(encoding="utf-8")
    assert bilingual_vtt.startswith("WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000")
    assert "<b>你好</b>\n<i>Hello & welcome</i>" in bilingual_vtt
    assert downloaded_languages == [("en-orig", "0"), ("zh-Hans", "56")]
    assert "Style: Chinese,Microsoft YaHei,57.6," in ass_paths[0].read_text(encoding="utf-8-sig")
