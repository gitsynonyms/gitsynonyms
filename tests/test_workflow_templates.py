import pytest

yaml = pytest.importorskip("yaml")

from pathlib import Path

ROOT = Path(__file__).parents[1]
STAGED = ROOT / ".github" / "workflows-deploy" / "process-single-video.yml"


def load_staged() -> dict:
    return yaml.safe_load(STAGED.read_text(encoding="utf-8"))


def test_staged_template_installs_cjk_and_latin_fonts():
    steps = load_staged()["jobs"]["process"]["steps"]
    install = [step for step in steps if str(step.get("name", "")).startswith("Install")][0]

    assert "fonts-noto-cjk" in install["run"]
    assert "fonts-dejavu-core" in install["run"]


def test_staged_template_drops_the_dead_style_preset_input():
    workflow = load_staged()

    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert "style_preset" not in inputs

    rendered = str(workflow["jobs"])
    assert "style_preset" not in rendered
    assert "--style-preset" not in rendered


def test_staged_template_keeps_the_documented_inputs():
    inputs = load_staged()[True]["workflow_dispatch"]["inputs"]

    assert list(inputs) == [
        "video_url",
        "output_dir",
        "state_file",
        "languages",
        "no_burn_in",
        "no_pages",
        "no_release",
        "force",
    ]
