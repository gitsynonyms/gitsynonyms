import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_video_pages.py"
SPEC = importlib.util.spec_from_file_location("generate_video_pages", SCRIPT)
pages = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pages
SPEC.loader.exec_module(pages)


def test_poster_lookup_is_per_video(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    (media / "lnOiG5dI4hg-poster.jpg").write_bytes(b"poster")
    (media / "TLH3gXrPCTU-poster.png").write_bytes(b"poster")
    monkeypatch.setattr(pages, "MEDIA_DIR", media)

    assert pages.poster_name("lnOiG5dI4hg") == "lnOiG5dI4hg-poster.jpg"
    assert pages.poster_name("TLH3gXrPCTU") == "TLH3gXrPCTU-poster.png"
    assert pages.poster_name("5E09UkJq4Jo") is None

    assert pages.poster_attribute("lnOiG5dI4hg", "../media/") == (
        ' poster="../media/lnOiG5dI4hg-poster.jpg"'
    )
    assert pages.poster_attribute("5E09UkJq4Jo", "../media/") == ""

    source = tmp_path / "video"
    source.mkdir()
    legacy = source / "bilingual_Chinese-English.ass"
    precise = source / "bilingual_precise_zh-Hans-en-orig.ass"
    legacy.write_text("legacy", encoding="utf-8")
    precise.write_text("precise", encoding="utf-8")
    monkeypatch.setattr(pages, "CAPTION_DIR", tmp_path / "site-captions")

    copied = pages.copy_bilingual("VIDEO", source)

    assert ("ass", "captions/VIDEO.bilingual.ass") in copied
    assert (tmp_path / "site-captions" / "VIDEO.bilingual.ass").read_text(
        encoding="utf-8"
    ) == "precise"
