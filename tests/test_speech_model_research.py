import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "speech_model_research.py"
SPEC = importlib.util.spec_from_file_location("speech_model_research", SCRIPT)
research = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = research
SPEC.loader.exec_module(research)


def test_snapshot_has_a_real_leaderboard():
    rows = research.load_rows()

    assert len(rows) >= 50
    assert all(row["wer"] > 0 for row in rows)
    assert any(row["model"] == "openai/whisper-large-v3" for row in rows)
    assert any("parakeet" in row["model"] for row in rows)


def test_ranking_is_sorted_and_has_at_least_ten_models():
    rows = research.rank(research.load_rows(), 15)

    assert len(rows) >= 10
    assert [row["wer"] for row in rows] == sorted(row["wer"] for row in rows)
    assert rows[0]["model"] == "OtterAI/speech-v3.1"


def test_open_weights_filter_hides_proprietary_services():
    rows = research.rank(research.load_rows(), 30, open_weights_only=True)

    assert rows
    assert all(row["license"] not in {"", "Proprietary", "NA"} for row in rows)
    assert all("OtterAI" not in row["model"] for row in rows)


def test_markdown_contains_a_ranked_table():
    rows = research.rank(research.load_rows(), 5)

    markdown = research.as_markdown(rows)

    assert "| Rank | Model | Avg WER | RTFx | License |" in markdown
    assert "OtterAI/speech-v3.1" in markdown
    assert "open_asr_leaderboard" in markdown


def test_cli_prints_json(tmp_path, capsys):
    assert research.main(["--top", "3", "--json"]) == 0

    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 3
    assert rows[0]["wer"] <= rows[1]["wer"]


def test_missing_snapshot_is_an_error(tmp_path, capsys):
    assert research.main(["--csv", str(tmp_path / "missing.csv")]) == 1
    assert "not found" in capsys.readouterr().err.lower()
