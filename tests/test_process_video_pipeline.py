import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "process_video_pipeline.py"
SPEC = importlib.util.spec_from_file_location("process_video_pipeline", SCRIPT)
pipeline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pipeline
SPEC.loader.exec_module(pipeline)


def test_require_tool_accepts_a_tool_on_path(monkeypatch):
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: f"/usr/bin/{name}")

    pipeline.require_tool("yt-dlp")


def test_require_tool_exits_when_the_tool_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: None)

    try:
        pipeline.require_tool("yt-dlp")
    except SystemExit as exit_error:
        assert exit_error.code == 1
    else:  # pragma: no cover - the guard must never fall through
        raise AssertionError("require_tool should exit when the tool is missing")

    assert "required tool 'yt-dlp' is not on PATH" in capsys.readouterr().err


def test_run_forwards_an_explicit_environment(monkeypatch, tmp_path):
    recorded = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        recorded["env"] = kwargs.get("env")
        return None

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    env = {"PATH": "/usr/bin", "GH_TOKEN": "token"}

    pipeline.run(["gh", "release", "view", "VIDEO"], env=env)

    assert recorded["command"] == ["gh", "release", "view", "VIDEO"]
    assert recorded["env"] == env


def test_step_output_is_written_only_inside_github_actions(monkeypatch, tmp_path):
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    pipeline.export_step_output("video_id", "lnOiG5dI4hg")

    assert output_file.read_text(encoding="utf-8") == "video_id=lnOiG5dI4hg\n"

    monkeypatch.delenv("GITHUB_OUTPUT")

    pipeline.export_step_output("video_id", "lnOiG5dI4hg")

    assert output_file.read_text(encoding="utf-8") == "video_id=lnOiG5dI4hg\n"


def test_video_id_parsing_covers_supported_url_shapes():
    assert pipeline.video_id_from_url("lnOiG5dI4hg") == "lnOiG5dI4hg"
    assert (
        pipeline.video_id_from_url("https://www.youtube.com/watch?v=lnOiG5dI4hg")
        == "lnOiG5dI4hg"
    )
    assert pipeline.video_id_from_url("https://youtu.be/lnOiG5dI4hg") == "lnOiG5dI4hg"
    assert (
        pipeline.video_id_from_url("https://www.youtube.com/live/lnOiG5dI4hg")
        == "lnOiG5dI4hg"
    )


def test_ledger_append_replaces_an_existing_record(tmp_path):
    state_file = tmp_path / "ledger.jsonl"
    state_file.write_text(
        json.dumps({"source_platform": "youtube", "source_id": "lnOiG5dI4hg"}) + "\n",
        encoding="utf-8",
    )

    pipeline.append_ledger(
        state_file=state_file,
        video_id="lnOiG5dI4hg",
        video_title="White House video",
        channel="The White House",
        channel_id="UCYxRlFDqcWM4y7FfpiAN3KQ",
        published_at="2026-10-23T20:00:00.000000000Z",
        published_date="2026-10-23",
        published_epoch_ns=1792872000000000000,
        time_source="timestamp",
        time_precision="second",
        burned_mp4=Path(""),
    )

    records = [
        json.loads(line)
        for line in state_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(records) == 1
    assert records[0]["source_id"] == "lnOiG5dI4hg"
    assert records[0]["processing_status"] == "published"
    assert records[0]["artifacts"] == [
        {
            "type": "captioned_video_release",
            "url": "https://github.com/gitsynonyms/gitsynonyms/releases/tag/lnOiG5dI4hg",
        }
    ]


def test_downloader_uses_the_https_first_video_format(monkeypatch, tmp_path):
    calls = []

    class FakeRun:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return FakeRun()

    folder = tmp_path / "2026-10-23_event_lnOiG5dI4hg"
    folder.mkdir()
    monkeypatch.setattr(pipeline, "run", fake_run)

    returned = pipeline.run_downloader(
        video_id="lnOiG5dI4hg",
        output_dir=tmp_path,
        languages="zh-Hans,en-orig",
    )

    assert returned == folder
    assert "--video-format" in calls[0]
    assert pipeline.DEFAULT_VIDEO_FORMAT in calls[0]
    assert "--burn-in" not in calls[0]


def test_burn_requires_the_preserved_source_video(tmp_path):
    folder = tmp_path / "2026-10-23_event_lnOiG5dI4hg"
    folder.mkdir()

    try:
        pipeline.burn_subtitles(
            folder=folder, video_id="lnOiG5dI4hg", languages="zh-Hans,en-orig"
        )
    except RuntimeError as error:
        assert "source video" in str(error)
    else:  # pragma: no cover - the guard must never fall through
        raise AssertionError("burn_subtitles should fail without a source video")


def test_processed_ids_ignore_other_platforms_and_broken_lines(tmp_path):
    state_file = tmp_path / "ledger.jsonl"
    state_file.write_text(
        "\n".join(
            [
                "# comment",
                "not json",
                json.dumps({"source_platform": "youtube", "source_id": "lnOiG5dI4hg"}),
                json.dumps({"source_platform": "vimeo", "source_id": "12345678901"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert pipeline.load_processed_ids(state_file) == {"lnOiG5dI4hg"}
