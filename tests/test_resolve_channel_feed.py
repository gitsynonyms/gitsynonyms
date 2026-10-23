import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "monitoring" / "resolve_channel_feed.py"
SPEC = importlib.util.spec_from_file_location("resolve_channel_feed", SCRIPT)
resolve_channel_feed = importlib.util.module_from_spec(SPEC)
sys.modules["resolve_channel_feed"] = resolve_channel_feed
SPEC.loader.exec_module(resolve_channel_feed)


def test_processed_video_ids_reads_legacy_and_json_records(tmp_path):
    state = tmp_path / "processed.txt"
    state.write_text(
        "oldVid00001\n"
        '{"source_platform":"youtube","source_id":"newVid00002"}\n'
        '{"source_platform":"other","source_id":"ignore000003"}\n',
        encoding="utf-8",
    )

    assert resolve_channel_feed.processed_video_ids(state) == {
        "oldVid00001",
        "newVid00002",
    }


def test_proxy_candidates_extracts_only_proxy_urls(tmp_path):
    index = tmp_path / "top10.json"
    index.write_text(
        json.dumps({"top": [{"proxy": "http://one:80"}, {"proxy": "http://two:80"}]}),
        encoding="utf-8",
    )

    assert resolve_channel_feed.proxy_candidates(index) == [
        "http://one:80",
        "http://two:80",
    ]


def test_proxy_candidates_rejects_malformed_index(tmp_path):
    index = tmp_path / "top10.json"
    index.write_text('{"top":{}}', encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid proxy index structure"):
        resolve_channel_feed.proxy_candidates(index)


def test_discover_video_skips_processed_and_checks_channel(tmp_path):
    processed = tmp_path / "processed.txt"
    processed.write_text("oldVid00001\n", encoding="utf-8")
    proxy_index = tmp_path / "top10.json"
    proxy_index.write_text('{"top":[]}', encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if "--flat-playlist" in command:
            channel = command[-1]
            listing = "oldVid00001\nnewVid00002\n" if channel.endswith("/videos") else ""
            return subprocess.CompletedProcess(command, 0, listing, "")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "id": "newVid00002",
                    "channel_id": resolve_channel_feed.CHANNEL_ID,
                }
            ),
            "",
        )

    metadata = resolve_channel_feed.discover_video(
        state_path=processed,
        proxy_index=proxy_index,
        runner=runner,
    )

    assert metadata["id"] == "newVid00002"
    assert len(calls) == 3


def test_run_ytdlp_retries_configured_proxy_after_direct_error():
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 1, "", "direct failed")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    assert resolve_channel_feed.run_ytdlp(["--version"], ["http://proxy:80"], runner=runner) == "ok"
    assert "--proxy" not in calls[0]
    assert calls[1][calls[1].index("--proxy") + 1] == "http://proxy:80"
