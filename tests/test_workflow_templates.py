import pytest

yaml = pytest.importorskip("yaml")

from pathlib import Path

ROOT = Path(__file__).parents[1]
STAGED = ROOT / ".github" / "workflows-deploy" / "process-single-video.yml"
ACTIVE = ROOT / ".github" / "workflows" / "process-single-video.yml"

# The only allowed difference between the archived template and the active
# workflow, as (removed_lines, added_lines) in document order. Once a maintainer
# installs the template the two files are identical and this list is unused.
EXPECTED_BLOCKS = [
    (
        (
            "style_preset:",
            "description: ASS style preset",
            "required: false",
            "type: string",
            'default: "classic"',
        ),
        (),
    ),
    (
        ("sudo apt-get update -y && sudo apt-get install -y ffmpeg",),
        (
            "sudo apt-get update -y",
            "# libass resolves the ASS families through fontconfig: without the CJK and",
            "# Latin packages the bilingual Chinese line burns in as missing glyphs.",
            "sudo apt-get install -y ffmpeg fonts-noto-cjk fonts-dejavu-core",
        ),
    ),
    (("STYLE_PRESET: ${{ inputs.style_preset }}",), ()),
    (('--style-preset "$STYLE_PRESET"',), ()),
]


def load_staged() -> dict:
    return yaml.safe_load(STAGED.read_text(encoding="utf-8"))


def changed_blocks() -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Return the (removed, added) line blocks between the active and staged files."""
    import difflib

    active = [line.strip() for line in ACTIVE.read_text(encoding="utf-8").splitlines()]
    staged = [line.strip() for line in STAGED.read_text(encoding="utf-8").splitlines()]
    matcher = difflib.SequenceMatcher(a=active, b=staged, autojunk=False)
    blocks = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        blocks.append((tuple(active[i1:i2]), tuple(staged[j1:j2])))
    return blocks


def test_staged_template_only_differs_by_the_documented_changes():
    """Guard against template drift (see infogap.yml, which already drifted).

    The archived template is a copy of the active workflow plus the documented
    changes. If the active workflow moves on without the template being re-cut,
    this test fails instead of letting a blind ``cp`` revert someone else's work.
    """
    assert changed_blocks() == EXPECTED_BLOCKS


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
