import json
import subprocess

from gitsynonyms import youtube
from gitsynonyms.youtube import _align_translations, _event_date, _parse_srt, _safe_slug, build_srt


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
    assert _align_translations(english, chinese) == ["你好"]


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
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(youtube, "_run_yt_dlp", fake_run)
    folder = youtube.save_youtube_archive(
        "https://www.youtube.com/watch?v=IySF_8OArvw", tmp_path
    )

    assert folder.name == "2026-10-15_president-trump-oct-15-2026_IySF_8OArvw"
    assert (folder / "auto-generated_English.srt").is_file()
    assert (folder / "auto-translated_Chinese-zh-Hans.srt").is_file()
    bilingual = (folder / "auto-translated_Bilingual.srt").read_text(encoding="utf-8-sig")
    assert "你好\nHello" in bilingual
