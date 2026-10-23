import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "third_party_captions.py"
SPEC = importlib.util.spec_from_file_location("third_party_captions", SCRIPT)
third_party = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = third_party
SPEC.loader.exec_module(third_party)


def test_registry_covers_at_least_ten_platforms():
    assert len(third_party.PROVIDERS) >= 10
    assert third_party.PROVIDERS["otter"]["adapter"] is None
    assert "No official public API" in third_party.PROVIDERS["otter"]["note"]
    assert third_party.PROVIDERS["openrouter"]["word_timestamps"] is False
    assert third_party.PROVIDERS["assemblyai"]["word_timestamps"] is True


def test_words_are_grouped_on_punctuation_and_long_gaps():
    words = [
        {"text": "Hello", "start": 1.0, "end": 1.2},
        {"text": "world.", "start": 1.3, "end": 1.6},
        {"text": "Next", "start": 3.0, "end": 3.2},
        {"text": "line", "start": 3.3, "end": 3.5},
    ]

    cues = third_party.cue_from_words(words)

    assert [cue["text"] for cue in cues] == ["Hello world.", "Next line"]
    assert cues[0]["start"] == 1.0
    assert cues[1]["start"] == 3.0


def test_cjk_words_are_joined_without_spaces():
    cues = third_party.cue_from_words(
        [
            {"text": "你", "start": 1.0, "end": 1.1},
            {"text": "好", "start": 1.1, "end": 1.2},
        ]
    )

    assert cues[0]["text"] == "你好"


def test_word_error_rate_and_alignment_offsets():
    assert third_party.word_error_rate(["hello", "world"], ["hello", "word"]) == 0.5
    assert third_party.word_error_rate(["hello"], ["hello"]) == 0.0
    assert third_party.word_error_rate([], []) == 0.0

    reference = [
        {"text": "hello", "start": 0.0, "end": 0.5},
        {"text": "world", "start": 1.0, "end": 1.5},
    ]
    hypothesis = [
        {"text": "hello", "start": 0.2, "end": 0.6},
        {"text": "world", "start": 1.1, "end": 1.6},
    ]
    offsets = third_party.alignment_offsets(reference, hypothesis)

    assert [round(value, 3) for value in offsets] == [0.2, 0.1]


def test_deepgram_adapter_normalizes_utterances(tmp_path, monkeypatch):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"audio")
    calls = []

    def fake_request(url, **kwargs):
        calls.append(url)
        return {
            "results": {
                "utterances": [
                    {
                        "start": 1.0,
                        "end": 2.5,
                        "transcript": "Hello world",
                        "words": [
                            {"text": "Hello", "start": 1.0, "end": 1.5},
                            {"text": "world", "start": 1.6, "end": 2.5},
                        ],
                    }
                ]
            }
        }

    monkeypatch.setattr(third_party, "_request", fake_request)

    result = third_party.run_deepgram(audio, "key", "en", 10, 0)

    assert result["cues"][0]["text"] == "Hello world"
    assert result["cues"][0]["start"] == 1.0
    assert result["words"][1]["start"] == 1.6
    assert "nova-3" in calls[0]


def test_assemblyai_adapter_uploads_and_polls(tmp_path, monkeypatch):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"audio")

    def fake_request(url, **kwargs):
        if url.endswith("/upload"):
            return {"upload_url": "https://cdn.example/audio.m4a"}
        if url.endswith("/transcript"):
            return {"id": "job-1"}
        return {
            "status": "completed",
            "utterances": [
                {
                    "start": 1000,
                    "end": 2500,
                    "text": "Hello world",
                    "words": [
                        {"text": "Hello", "start": 1000, "end": 1500},
                        {"text": "world", "start": 1600, "end": 2500},
                    ],
                }
            ],
        }

    monkeypatch.setattr(third_party, "_request", fake_request)

    result = third_party.run_assemblyai(audio, "key", "en", 10, 0)

    assert result["cues"][0]["text"] == "Hello world"
    assert result["words"][1]["start"] == 1.6


def test_compare_provider_reports_wer_offset_and_cost(tmp_path, monkeypatch):
    reference = {
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.5},
            {"text": "world", "start": 1.0, "end": 1.5},
        ],
        "cues": [],
    }
    result = {
        "cues": [
            {"start": 0.1, "end": 1.8, "text": "hello word", "words": []},
        ],
        "words": [
            {"text": "hello", "start": 0.1, "end": 0.6},
            {"text": "word", "start": 1.1, "end": 1.8},
        ],
        "model": "test",
    }

    record = third_party.compare_provider(
        "deepgram", result, reference, duration=3600, language="en"
    )

    assert record["wer_percent"] == 50.0
    assert record["timestamp_median_offset_ms"] == 100.0
    assert record["cost_usd"] == 0.288
    assert record["cues"] == 1


def test_missing_keys_are_reported_not_faked(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    work = tmp_path / "work"

    code = third_party.main(
        ["--providers", "deepgram", "--work-dir", str(work), "--json"]
    )

    report = json.loads((work / "COMPARISON.json").read_text(encoding="utf-8"))
    assert code == 0
    assert report["providers"]["deepgram"]["status"] == "skipped"
    assert "DEEPGRAM_API_KEY" in report["providers"]["deepgram"]["reason"]
    assert not (work / "deepgram" / "deepgram.srt").exists()


def test_prepare_audio_extracts_video_audio_and_limits_the_excerpt(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"audio")

    monkeypatch.setattr(third_party.subprocess, "run", fake_run)

    result = third_party.prepare_audio(
        audio=source,
        url=None,
        work_dir=tmp_path / "work",
        limit_seconds=180,
    )

    assert result == tmp_path / "work" / "excerpt.m4a"
    assert len(commands) == 2
    assert "-vn" in commands[0]
    assert commands[0][-1].endswith("audio.m4a")
    assert commands[1][commands[1].index("-t") + 1] == "180.000"


def test_main_uses_reference_language_separate_from_provider_language(
    tmp_path, monkeypatch
):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"audio")
    captured = {}
    reference = {
        "kind": "fused-caption-folder",
        "path": "captions",
        "formats": ["json3"],
        "cues": [
            {
                "start": 0.0,
                "end": 0.5,
                "text": "hello",
                "words": [{"text": "hello", "start": 0.0, "end": 0.5}],
            }
        ],
        "words": [{"text": "hello", "start": 0.0, "end": 0.5}],
    }

    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setattr(third_party, "prepare_audio", lambda **_kwargs: audio)
    monkeypatch.setattr(third_party, "probe_duration", lambda _path: 0.5)

    def fake_load_reference(_path, *, language, video_id):
        captured["reference_language"] = language
        captured["video_id"] = video_id
        return reference

    monkeypatch.setattr(third_party, "load_reference", fake_load_reference)
    monkeypatch.setitem(
        third_party.ADAPTERS,
        "deepgram",
        lambda *_args: {
            "cues": [{"start": 0.0, "end": 0.5, "text": "hello", "words": []}],
            "words": [{"text": "hello", "start": 0.0, "end": 0.5}],
            "model": "test",
        },
    )
    work = tmp_path / "comparison"

    assert third_party.main(
        [
            "--audio",
            str(audio),
            "--reference",
            "captions",
            "--reference-language",
            "en-orig",
            "--video-id",
            "VIDEO",
            "--language",
            "en",
            "--providers",
            "deepgram",
            "--work-dir",
            str(work),
        ]
    ) == 0

    report = json.loads((work / "COMPARISON.json").read_text(encoding="utf-8"))
    assert captured == {"reference_language": "en-orig", "video_id": "VIDEO"}
    assert report["language"] == "en"
    assert report["reference"]["language"] == "en-orig"
    assert report["providers"]["deepgram"]["wer_percent"] == 0.0


def test_srt_output_is_numbered_and_millisecond(tmp_path):
    path = tmp_path / "out.srt"

    third_party.write_srt(
        [{"start": 1.005, "end": 2.5, "text": "Hello"}], path
    )

    assert path.read_text(encoding="utf-8") == "1\n00:00:01,005 --> 00:00:02,500\nHello\n"
