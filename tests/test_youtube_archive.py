import json
import subprocess
from pathlib import Path

from gitsynonyms import youtube
from gitsynonyms.youtube import (
    _event_date,
    _parse_srt,
    _safe_slug,
    _build_synchronized_srt,
    build_srt,
)


def test_build_srt_places_chinese_above_english():
    srt = build_srt(
        [{"start": 0, "duration": 1.25, "text": "Hello"}],
        chinese=["你好"],
    )
    assert "00:00:00,000 --> 00:00:01,250\n你好\nHello" in srt


def test_build_srt_rejects_misaligned_bilingual_entries():
    try:
        build_srt([{"start": 0, "duration": 1, "text": "Hello"}], chinese=[])
    except ValueError as error:
        assert "matching counts" in str(error)
    else:
        raise AssertionError("Expected mismatched bilingual entries to fail")


def test_event_metadata_folder_parts():
    assert _event_date("President Trump, Oct. 15, 2026") == "2026-10-15"
    assert _safe_slug("President Trump: Rose Garden Lunch!") == "president-trump-rose-garden-lunch"


def test_parse_and_align_srt_entries():
    english = _parse_srt("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n")
    chinese = _parse_srt("1\n00:00:01,000 --> 00:00:02,000\n你好\n\n")
    assert english[0]["text"] == "Hello"
    assert _build_synchronized_srt(chinese, english) == (
        "1\n00:00:01,000 --> 00:00:02,000\n你好\nHello\n"
    )


def test_parse_srt_keeps_caption_markup_and_spacing():
    parsed = _parse_srt("1\n00:00:01,000 --> 00:00:02,000\n  <b>Text</b>  \n\n")
    assert parsed[0]["text"] == "  <b>Text</b>  "


def test_fetch_transcript_defaults_to_yt_dlp_original_english_track(monkeypatch):
    metadata = {
        "subtitles": {"en-orig": [], "en": []},
        "automatic_captions": {"en": [], "en-orig": []},
    }
    downloaded = []
    expected = [
        {"start": 1.0, "duration": 2.0, "text": "A phrase shown in the live captions."}
    ]
    monkeypatch.setattr(
        youtube,
        "_run_yt_dlp",
        lambda _args: subprocess.CompletedProcess([], 0, json.dumps(metadata), ""),
    )

    def fake_download(url, video_id, language, kind, directory, sleep_seconds=56):
        downloaded.append((url, video_id, language, kind, sleep_seconds, directory))
        return directory / "source.srt", expected

    monkeypatch.setattr(youtube, "_download_yt_dlp_track", fake_download)
    result = youtube.fetch_transcript("https://youtu.be/IySF_8OArvw")

    assert result == expected
    assert downloaded[0][:5] == (
        "https://youtu.be/IySF_8OArvw",
        "IySF_8OArvw",
        "en-orig",
        "auto-generated",
        0,
    )


def test_fetch_transcript_uses_manual_track_only_when_no_automatic_track(monkeypatch):
    metadata = {
        "subtitles": {"en-orig": [], "en": []},
        "automatic_captions": {},
    }
    downloaded = []
    monkeypatch.setattr(
        youtube,
        "_run_yt_dlp",
        lambda _args: subprocess.CompletedProcess([], 0, json.dumps(metadata), ""),
    )

    def fake_download(_url, _video_id, language, kind, _directory, sleep_seconds=56):
        downloaded.append((language, kind, sleep_seconds))
        return Path("manual.srt"), [
            {"start": 0.0, "duration": 1.0, "text": "Manual fallback"}
        ]

    monkeypatch.setattr(youtube, "_download_yt_dlp_track", fake_download)
    entries = youtube.fetch_transcript("IySF_8OArvw")

    assert entries[0]["text"] == "Manual fallback"
    assert downloaded == [("en-orig", "manual", 0)]


def test_fetch_transcript_can_explicitly_use_transcript_api(monkeypatch):
    expected = [{"start": 1.0, "duration": 2.0, "text": "Transcript API text"}]

    class FakeApi:
        def fetch(self, video_id, *, languages):
            assert video_id == "IySF_8OArvw"
            assert languages == ["en"]
            return [type("Entry", (), expected[0])()]

    monkeypatch.setattr(youtube, "YouTubeTranscriptApi", FakeApi)
    assert youtube.fetch_transcript(
        "https://youtu.be/IySF_8OArvw", source="transcript-api"
    ) == expected


def test_fetch_transcript_does_not_silently_fallback_when_track_missing(monkeypatch):
    metadata = {"subtitles": {}, "automatic_captions": {"fr": []}}
    monkeypatch.setattr(
        youtube,
        "_run_yt_dlp",
        lambda _args: subprocess.CompletedProcess([], 0, json.dumps(metadata), ""),
    )
    try:
        youtube.fetch_transcript("IySF_8OArvw")
    except youtube.SubtitleTranslationError as error:
        assert "en-orig, en" in str(error)
    else:
        raise AssertionError("Expected a missing English source track to be reported")


def test_translate_text_reports_failure_instead_of_returning_source_text():
    class BrokenTranslator:
        def translate(self, _text):
            raise RuntimeError("service unavailable")

    try:
        youtube.translate_text("Original words", translator=BrokenTranslator())
    except youtube.SubtitleTranslationError as error:
        assert "service unavailable" in str(error)
    else:
        raise AssertionError("Expected translation failure to be visible")


def test_bilingual_srt_splits_cues_at_source_boundaries():
    chinese = [{"start": 1, "duration": 2, "text": "中文"}]
    english = [{"start": 2, "duration": 2, "text": "English"}]
    assert _build_synchronized_srt(chinese, english) == (
        "1\n00:00:01,000 --> 00:00:02,000\n中文\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n中文\nEnglish\n\n"
        "3\n00:00:03,000 --> 00:00:04,000\nEnglish\n"
    )


def test_bilingual_srt_preserves_english_rolling_order_in_one_block():
    chinese = [{"start": 1, "duration": 4, "text": "中文"}]
    english = [
        {"start": 1, "duration": 3, "text": "Earlier caption"},
        {"start": 2, "duration": 3, "text": "Latest caption"},
    ]
    merged = _build_synchronized_srt(chinese, english)
    assert (
        "00:00:02,000 --> 00:00:04,000\n"
        "中文\n"
        "Earlier caption\nLatest caption"
    ) in merged


def test_extract_youtube_url_variants_and_reject_unrelated_host():
    assert youtube.extract_video_id("https://youtu.be/IySF_8OArvw") == "IySF_8OArvw"
    assert youtube.extract_video_id("https://www.youtube.com/live/IySF_8OArvw") == "IySF_8OArvw"
    try:
        youtube.extract_video_id("https://notyoutube.com/watch?v=IySF_8OArvw")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected an unrelated host to be rejected")


def test_find_track_is_case_insensitive():
    assert youtube._find_track(["zh-hans"], {"zh-Hans": []}) == "zh-Hans"


def test_archive_saves_separate_labeled_files(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube.shutil, "which", lambda _: "yt-dlp.exe")
    original_tracks = {}

    def fake_run(arguments):
        if "--dump-single-json" in arguments:
            metadata = {
                "id": "IySF_8OArvw",
                "title": "President Trump, Oct. 15, 2026",
                "upload_date": "20261015",
                "subtitles": {},
                "automatic_captions": {"en": [], "zh-Hans": []},
            }
            return subprocess.CompletedProcess([], 0, json.dumps(metadata), "")
        language = arguments[arguments.index("--sub-langs") + 1]
        assert "--convert-subs" in arguments
        if language == "zh-Hans":
            assert arguments[arguments.index("--sleep-subtitles") + 1] == "56"
        template = arguments[arguments.index("-o") + 1]
        file_path = template.replace("%(id)s", "IySF_8OArvw").replace(
            "%(language)s", language
        ).replace("%(ext)s", "srt")
        text = "Hello" if language == "en" else "你好"
        with open(file_path, "w", encoding="utf-8") as output:
            output.write(f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n\n")
        original_tracks[language] = Path(file_path).read_bytes()
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(youtube, "_run_yt_dlp", fake_run)
    folder = youtube.save_youtube_archive(
        "https://www.youtube.com/watch?v=IySF_8OArvw", tmp_path
    )

    assert folder.name == "2026-10-15_president-trump-oct-15-2026_IySF_8OArvw"
    assert (folder / "auto-generated_English.srt").is_file()
    assert (folder / "auto-translated_Chinese-zh-Hans.srt").is_file()
    assert (folder / "auto-generated_English.srt").read_bytes() == original_tracks["en"]
    bilingual = (folder / "auto-translated_Bilingual.srt").read_text(encoding="utf-8-sig")
    assert "你好\nHello" in bilingual
