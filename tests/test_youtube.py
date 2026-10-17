from gitsynonyms.youtube import build_bilingual_srt, extract_video_id


class FakeTranslator:
    def translate(self, text):
        return "你好，世界"


def test_extract_video_id_from_watch_url():
    assert extract_video_id("https://www.youtube.com/watch?v=IySF_8OArvw") == "IySF_8OArvw"


def test_build_bilingual_srt_uses_chinese_above_english():
    entries = [
        {"start": 0.0, "duration": 2.0, "text": "hello world"},
    ]
    srt = build_bilingual_srt(entries, target_lang="zh-CN", translator=FakeTranslator())
    lines = srt.strip().splitlines()
    assert lines[2] == "你好，世界"
    assert lines[3] == "hello world"
