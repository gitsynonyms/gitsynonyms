import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_video_pages.py"
SPEC = importlib.util.spec_from_file_location("generate_video_pages", SCRIPT)
pages = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pages
SPEC.loader.exec_module(pages)


def test_pages_publish_the_precise_ass_instead_of_the_legacy_ass(tmp_path, monkeypatch):
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
