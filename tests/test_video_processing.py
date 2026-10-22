import importlib.util
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "video_processing.py"
SPEC = importlib.util.spec_from_file_location("video_processing", SCRIPT)
video_processing = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = video_processing
SPEC.loader.exec_module(video_processing)


SRT = "1\n00:00:01,000 --> 00:00:03,000\nHello\n\n"
ASS = r"""[Script Info]
PlayResX: 640
PlayResY: 360

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, Alignment, MarginL, MarginR, MarginV
Style: Default,Arial,32,&H00FFFFFF,5,10,10,10
Style: CaptionBox,DejaVu Sans,1,&H64000000,7,0,0,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello
Dialogue: 0,0:00:00.00,0:00:10.00,CaptionBox,,0,0,0,,{\an7\pos(0,0)\p1}m 0 0 l 10 0 10 10 0 10{\p0}
"""


def probe_json(duration=4.0, width=640, height=360, audio_codec="aac"):
    return {
        "format": {"duration": str(duration), "size": "1024", "format_name": "mov,mp4"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": width,
                "height": height,
                "avg_frame_rate": "25/1",
                "pix_fmt": "yuv420p",
            },
            {"codec_type": "audio", "codec_name": audio_codec, "channels": 2, "sample_rate": "48000"},
        ],
    }


def make_selection(tmp_path, subtitle_name="captions.srt", text=SRT, **kwargs):
    video = tmp_path / "TLH3gXrPCTU.source.mp4"
    video.write_bytes(b"video")
    subtitle = tmp_path / subtitle_name
    subtitle.write_text(text, encoding="utf-8")
    selection = video_processing.Selection(
        video=video,
        subtitle=subtitle,
        output=kwargs.pop("output", tmp_path / "Burned_Bilingual_TLH3gXrPCTU.mp4"),
        video_id="TLH3gXrPCTU",
        output_dir=tmp_path,
        **kwargs,
    )
    return selection


def install_fake_ffmpeg(monkeypatch, tmp_path, source_duration=4.0, output_duration=4.0, stderr="", audio_codec="aac", fail_burn=False):
    """Replace ffmpeg/ffprobe with a recorder that writes plausible outputs."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if "-version" in command:
            return subprocess.CompletedProcess(command, 0, "ffmpeg version test-build\n", "")
        if command[0] == "ffprobe":
            target = Path(command[-1])
            duration = source_duration if target.suffix == ".mp4" else source_duration
            if "Burned_" in target.name:
                duration = output_duration
            return subprocess.CompletedProcess(
                command, 0, json.dumps(probe_json(duration, audio_codec=audio_codec)), ""
            )
        if fail_burn:
            raise subprocess.CalledProcessError(1, command, "", "encoder exploded")
        Path(command[-1]).write_bytes(b"rendered-media")
        return subprocess.CompletedProcess(command, 0, "", stderr)

    monkeypatch.setattr(video_processing.shutil, "which", lambda name: name)
    monkeypatch.setattr(video_processing.subprocess, "run", fake_run)
    return calls


def write_manifest(tmp_path, **values):
    manifest = tmp_path / "selection.json"
    manifest.write_text(json.dumps(values), encoding="utf-8")
    return manifest


def test_manifest_prefers_ass_and_resolves_paths_against_itself(tmp_path):
    (tmp_path / "video").mkdir()
    (tmp_path / "subs").mkdir()
    (tmp_path / "video" / "TLH3gXrPCTU.source.mp4").write_bytes(b"video")
    for extension, text in (("srt", SRT), ("vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello\n"), ("ass", ASS)):
        (tmp_path / "subs" / f"bilingual.{extension}").write_text(text, encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        video="video/TLH3gXrPCTU.source.mp4",
        subtitles={"srt": "subs/bilingual.srt", "ass": "subs/bilingual.ass"},
        output_dir="dist",
        preview=True,
        poster=True,
        title="Roundtable",
        event_date="2026-10-21",
    )

    selection = video_processing.selection_from_manifest(manifest)

    assert selection.video == (tmp_path / "video" / "TLH3gXrPCTU.source.mp4").resolve()
    assert selection.subtitle == (tmp_path / "subs" / "bilingual.ass").resolve()
    assert selection.video_id == "TLH3gXrPCTU"
    assert selection.output == (tmp_path / "dist" / "Burned_Bilingual_TLH3gXrPCTU.mp4").resolve()
    assert selection.preview.name == "Burned_Bilingual_TLH3gXrPCTU-540p.mp4"
    assert selection.poster.name == "Burned_Bilingual_TLH3gXrPCTU-poster.jpg"
    assert selection.title == "Roundtable"
    assert selection.event_date == "2026-10-21"


def test_manifest_falls_back_to_the_subtitle_that_exists(tmp_path):
    (tmp_path / "TLH3gXrPCTU.source.mp4").write_bytes(b"video")
    (tmp_path / "captions.srt").write_text(SRT, encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        video_id="TLH3gXrPCTU",
        video="TLH3gXrPCTU.source.mp4",
        subtitles={"ass": "missing.ass", "srt": "captions.srt"},
    )

    selection = video_processing.selection_from_manifest(manifest)

    assert selection.subtitle.name == "captions.srt"
    assert selection.output == (tmp_path / "Burned_Bilingual_TLH3gXrPCTU.mp4").resolve()


def test_manifest_requires_a_video_and_a_subtitle(tmp_path):
    manifest = write_manifest(tmp_path, video_id="TLH3gXrPCTU")
    try:
        video_processing.selection_from_manifest(manifest)
    except ValueError as error:
        assert "video" in str(error)
    else:
        raise AssertionError("a manifest without a video must fail")

    (tmp_path / "TLH3gXrPCTU.source.mp4").write_bytes(b"video")
    manifest = write_manifest(tmp_path, video="TLH3gXrPCTU.source.mp4")
    try:
        video_processing.selection_from_manifest(manifest)
    except ValueError as error:
        assert "subtitle" in str(error)
    else:
        raise AssertionError("a manifest without a subtitle must fail")


def test_escape_filter_path_protects_the_libass_argument():
    escaped = video_processing.escape_filter_path(Path("/tmp/a:b,c[d]'e.ass"))

    assert escaped == r"/tmp/a\:b\,c\[d\]\'e.ass"


def test_burn_command_uses_one_libass_pass_and_copies_audio(tmp_path):
    command = video_processing.build_burn_command(
        "ffmpeg",
        tmp_path / "source.mp4",
        tmp_path / "captions.ass",
        tmp_path / "burned.mp4",
    )

    assert command[command.index("-vf") + 1].startswith("subtitles=filename='")
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-crf") + 1] == "20"
    assert command[command.index("-preset") + 1] == "veryfast"
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[command.index("-movflags") + 1] == "+faststart"
    assert command[-1].endswith("burned.mp4")


def test_qsv_burn_uses_global_quality(tmp_path):
    command = video_processing.build_burn_command(
        "ffmpeg",
        tmp_path / "source.mp4",
        tmp_path / "captions.ass",
        tmp_path / "burned.mp4",
        encoder="h264_qsv",
        crf=1,
    )

    assert command[command.index("-c:v") + 1] == "h264_qsv"
    assert command[command.index("-global_quality") + 1] == "1"
    assert "-crf" not in command


def test_burn_command_validates_quality_and_encoder(tmp_path):
    for values in ({"encoder": "mpeg4"}, {"crf": 0}, {"preset": " "}):
        try:
            video_processing.build_burn_command(
                "ffmpeg",
                tmp_path / "source.mp4",
                tmp_path / "captions.ass",
                tmp_path / "burned.mp4",
                **values,
            )
        except ValueError:
            continue
        raise AssertionError(f"invalid burn options must fail: {values}")


def test_ass_inspection_ignores_drawing_events_for_timing(tmp_path):
    ass = tmp_path / "captions.ass"
    ass.write_text(ASS, encoding="utf-8")

    info = video_processing.inspect_subtitles(ass, 2.0)

    assert info["format"] == "ass"
    assert info["cues"] == 1
    assert info["drawing_cues"] == 1
    assert info["first_cue_seconds"] == 1.0
    assert info["last_cue_seconds"] == 3.0
    assert info["overrun_seconds"] == 1.0
    assert info["fonts"] == ["Arial", "DejaVu Sans"]


def test_ass_fonts_follow_the_format_line_order(tmp_path):
    ass = tmp_path / "reordered.ass"
    ass.write_text(
        "[V4+ Styles]\n"
        "Format: Name, Fontsize, Fontname, PrimaryColour\n"
        "Style: Chinese,57.6,Noto Sans CJK SC,&H0028C9FF\n",
        encoding="utf-8",
    )

    assert video_processing.ass_fonts(ass) == ["Noto Sans CJK SC"]


def test_subtitle_parser_rejects_an_empty_track(tmp_path):
    empty = tmp_path / "empty.srt"
    empty.write_text("", encoding="utf-8")
    try:
        video_processing.inspect_subtitles(empty, 10.0)
    except ValueError as error:
        assert "no" in str(error).lower() or "SRT" in str(error)
    else:
        raise AssertionError("an empty subtitle track must fail")


def test_font_check_reports_a_substituted_family(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "Noto Sans\n", "")

    monkeypatch.setattr(video_processing.shutil, "which", lambda name: "fc-match")
    monkeypatch.setattr(video_processing.subprocess, "run", fake_run)

    report = video_processing.check_fonts(["Arial"])

    assert report["checked"] is True
    assert report["fonts"] == [{"requested": "Arial", "resolved": "Noto Sans", "exact": False}]


def test_preview_and_poster_commands_preserve_aspect_and_never_upscale(tmp_path):
    preview = video_processing.build_preview_command(
        "ffmpeg", tmp_path / "burned.mp4", tmp_path / "preview.mp4", source_height=1080
    )
    small = video_processing.build_preview_command(
        "ffmpeg", tmp_path / "burned.mp4", tmp_path / "preview.mp4", source_height=360
    )
    poster = video_processing.build_poster_command(
        "ffmpeg", tmp_path / "burned.mp4", tmp_path / "poster.jpg", at_seconds=12.5
    )

    assert preview[preview.index("-vf") + 1] == "scale=-2:540:flags=lanczos"
    assert preview[preview.index("-c:a") + 1] == "aac"
    assert small[small.index("-vf") + 1] == "scale=-2:360:flags=lanczos"
    assert poster[poster.index("-ss") + 1] == "12.500"
    assert poster[poster.index("-frames:v") + 1] == "1"


def test_process_burns_the_selection_and_writes_the_handoff(tmp_path, monkeypatch):
    selection = make_selection(
        tmp_path,
        preview=tmp_path / "Burned_Bilingual_TLH3gXrPCTU-540p.mp4",
        poster=tmp_path / "Burned_Bilingual_TLH3gXrPCTU-poster.jpg",
    )
    calls = install_fake_ffmpeg(
        monkeypatch, tmp_path, stderr="[libass] Fontconfig warning: fallback used"
    )

    report = video_processing.process(selection, video_processing.BurnOptions(check_fonts=False))

    assert report["status"] == "burned"
    assert report["subtitles"]["cues"] == 1
    assert report["video"]["duration_seconds"] == 4.0
    assert report["outputs"]["video"]["rendered"] is True
    assert report["outputs"]["preview"]["rendered"] is True
    assert report["outputs"]["poster"]["rendered"] is True
    assert report["burn"]["encoder"] == "libx264"
    assert any("Fontconfig" in warning for warning in report["warnings"])
    assert len([call for call in calls if "-version" not in call]) == 5  # 2 probes + burn + preview + poster
    stored = json.loads((tmp_path / "BURN.json").read_text(encoding="utf-8"))
    assert stored["module"] == "module2-video-processing"
    assert stored["status"] == "burned"
    assert stored["inputs"]["video"]["sha256"] == report["inputs"]["video"]["sha256"]


def test_process_reuses_a_fresh_render(tmp_path, monkeypatch):
    import os

    selection = make_selection(tmp_path)
    selection.output.write_bytes(b"already rendered")
    future = selection.output.stat().st_mtime + 60
    os.utime(selection.output, (future, future))
    calls = install_fake_ffmpeg(monkeypatch, tmp_path)

    report = video_processing.process(selection, video_processing.BurnOptions(check_fonts=False))

    assert report["status"] == "reused"
    assert report["burn"]["command"] is None
    assert "-vf" not in [option for call in calls for option in call]
    assert report["outputs"]["video"]["rendered"] is False


def test_dry_run_validates_but_does_not_render(tmp_path, monkeypatch):
    selection = make_selection(tmp_path, preview=tmp_path / "preview.mp4")
    install_fake_ffmpeg(monkeypatch, tmp_path)

    report = video_processing.process(
        selection, video_processing.BurnOptions(check_fonts=False, dry_run=True)
    )

    assert report["status"] == "dry-run"
    assert not selection.output.exists()
    assert report["outputs"]["video"]["rendered"] is False
    assert report["outputs"]["preview"]["command"][-1].endswith("preview.mp4")
    assert "-vf" in report["burn"]["command"]


def test_strict_overrun_fails_and_records_the_error(tmp_path, monkeypatch):
    selection = make_selection(tmp_path, text="1\n00:00:01,000 --> 00:00:03,000\nHello\n\n")
    install_fake_ffmpeg(monkeypatch, tmp_path, source_duration=2.0)

    try:
        video_processing.process(
            selection, video_processing.BurnOptions(check_fonts=False, strict=True)
        )
    except ValueError as error:
        assert "past the end" in str(error)
    else:
        raise AssertionError("a subtitle past the video end must fail in strict mode")

    report = json.loads((tmp_path / "BURN.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert "past the end" in report["error"]
    assert not selection.output.exists()


def test_main_wires_the_manifest_to_the_handoff(tmp_path, monkeypatch, capsys):
    (tmp_path / "TLH3gXrPCTU.source.mp4").write_bytes(b"video")
    (tmp_path / "captions.srt").write_text(SRT, encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        video_id="TLH3gXrPCTU",
        event_date="2026-10-21",
        video="TLH3gXrPCTU.source.mp4",
        subtitles={"srt": "captions.srt"},
    )
    install_fake_ffmpeg(monkeypatch, tmp_path)

    assert video_processing.main(["--selection", str(manifest), "--no-font-check"]) == 0

    report = json.loads((tmp_path / "BURN.json").read_text(encoding="utf-8"))
    assert report["status"] == "burned"
    assert report["selection"]["event_date"] == "2026-10-21"
    assert report["outputs"]["video"]["path"] == str(
        tmp_path / "Burned_Bilingual_TLH3gXrPCTU.mp4"
    )
    assert "Module 2" in capsys.readouterr().out


def test_main_reports_a_missing_manifest(tmp_path, capsys):
    assert video_processing.main(["--selection", str(tmp_path / "missing.json")]) == 1
    assert "manifest not found" in capsys.readouterr().err.lower()


def test_main_requires_both_paths_without_a_manifest(capsys):
    assert video_processing.main(["--video", "source.mp4"]) == 1
    assert "--video and --subtitle" in capsys.readouterr().err


def test_render_log_deduplicates_font_noise_and_counts_missing_glyphs():
    stderr = "\n".join(
        [
            "[Parsed_subtitles_0 @ 0x1] Fontconfig error: Cannot load default config file",
            "[Parsed_subtitles_0 @ 0x1] Fontconfig error: Cannot load default config file",
            "[Parsed_subtitles_0 @ 0x2] fontselect: (Arial, 400, 0) -> DejaVuSans.ttf",
            "[Parsed_subtitles_0 @ 0x2] Glyph 0x5973 not found, selecting one more font",
            "[Parsed_subtitles_0 @ 0x2] Glyph 0x5973 not found, selecting one more font",
            "[Parsed_subtitles_0 @ 0x2] Glyph 0x5B57 not found, selecting one more font",
        ]
    )

    missing, warnings = video_processing.summarize_render_log(stderr)

    assert missing == 2
    assert len([item for item in warnings if "Fontconfig error" in item]) == 1
    assert all("Glyph 0x" not in item for item in warnings)
    assert any("2 characters" in item for item in warnings)


def test_strict_fonts_fails_the_burn_when_glyphs_are_missing(tmp_path, monkeypatch):
    selection = make_selection(tmp_path)
    install_fake_ffmpeg(
        monkeypatch,
        tmp_path,
        stderr="[Parsed_subtitles_0 @ 0x1] Glyph 0x5973 not found, selecting one more font",
    )

    try:
        video_processing.process(
            selection,
            video_processing.BurnOptions(check_fonts=False, strict_fonts=True),
        )
    except RuntimeError as error:
        assert "missing glyphs" in str(error)
    else:
        raise AssertionError("strict font mode must fail when glyphs are missing")

    report = json.loads((tmp_path / "BURN.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["render"]["missing_glyphs"] == 1


def test_mp4_audio_is_reencoded_when_copy_is_not_allowed(tmp_path, monkeypatch):
    selection = make_selection(tmp_path)
    install_fake_ffmpeg(monkeypatch, tmp_path, audio_codec="opus")

    report = video_processing.process(selection, video_processing.BurnOptions(check_fonts=False))

    command = report["burn"]["command"]
    assert report["burn"]["audio"] == "aac"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "192k"
    assert report["status"] == "burned"


def test_mkv_target_keeps_the_original_audio(tmp_path):
    output = tmp_path / "burned.mkv"
    assert video_processing.choose_audio_mode(output, {"codec": "opus"}) == "copy"
    assert video_processing.choose_audio_mode(tmp_path / "b.mp4", {"codec": "opus"}) == "aac"
    assert video_processing.choose_audio_mode(tmp_path / "b.mp4", {"codec": "aac"}) == "copy"
    assert video_processing.choose_audio_mode(tmp_path / "b.mp4", None) == "copy"


def test_failed_burn_leaves_neither_output_nor_partial_file(tmp_path, monkeypatch):
    selection = make_selection(tmp_path)
    install_fake_ffmpeg(monkeypatch, tmp_path, fail_burn=True)

    try:
        video_processing.process(selection, video_processing.BurnOptions(check_fonts=False))
    except RuntimeError as error:
        assert "encoder exploded" in str(error)
    else:
        raise AssertionError("a failed FFmpeg run must fail the module")

    assert not selection.output.exists()
    assert not list(tmp_path.glob(".*part*"))
    report = json.loads((tmp_path / "BURN.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert "encoder exploded" in report["error"]


def test_successful_burn_replaces_the_partial_file(tmp_path, monkeypatch):
    selection = make_selection(tmp_path)
    install_fake_ffmpeg(monkeypatch, tmp_path)

    video_processing.process(selection, video_processing.BurnOptions(check_fonts=False))

    assert selection.output.read_bytes() == b"rendered-media"
    assert not list(tmp_path.glob(".*part*"))


def test_output_paths_may_not_overwrite_the_selection(tmp_path, monkeypatch):
    selection = make_selection(tmp_path, output=tmp_path / "TLH3gXrPCTU.source.mp4")
    install_fake_ffmpeg(monkeypatch, tmp_path)

    try:
        video_processing.process(selection, video_processing.BurnOptions(check_fonts=False))
    except ValueError as error:
        assert "overwrite" in str(error)
    else:
        raise AssertionError("writing over the selected video must fail")

    assert selection.video.read_bytes() == b"video"


def test_preview_may_not_collide_with_the_burn_output(tmp_path, monkeypatch):
    output = tmp_path / "Burned_Bilingual_TLH3gXrPCTU.mp4"
    selection = make_selection(tmp_path, output=output, preview=output)
    install_fake_ffmpeg(monkeypatch, tmp_path)

    try:
        video_processing.process(selection, video_processing.BurnOptions(check_fonts=False))
    except ValueError as error:
        assert "same file" in str(error)
    else:
        raise AssertionError("colliding outputs must fail")
