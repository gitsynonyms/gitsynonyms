import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "monitoring" / "install_workflow.py"
SPEC = importlib.util.spec_from_file_location("install_workflow", SCRIPT)
install_workflow = importlib.util.module_from_spec(SPEC)
sys.modules["install_workflow"] = install_workflow
SPEC.loader.exec_module(install_workflow)


def prepare(tmp_path, monkeypatch, bodies=None):
    bodies = bodies or {}
    for source, name in install_workflow.WORKFLOWS.values():
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bodies.get(name, f"name: {name}\n"), encoding="utf-8")
    monkeypatch.setattr(install_workflow, "ROOT", tmp_path)
    return tmp_path / ".github" / "workflows"


def test_install_copies_every_tracked_workflow_source(tmp_path, monkeypatch):
    targets = prepare(tmp_path, monkeypatch)

    install_workflow.install("proxy-scan")
    assert (targets / "proxy-scan.yml").read_text(encoding="utf-8") == "name: proxy-scan.yml\n"

    assert install_workflow.main(["all"]) == 0
    for name in install_workflow.SETTLED:
        assert (targets / install_workflow.WORKFLOWS[name][1]).is_file()
    # The still-unmerged template must not be swept in by "all".
    assert not (targets / "process-single-video.yml").exists()


def test_the_end_to_end_template_installs_from_the_deploy_directory():
    source, name = install_workflow.WORKFLOWS["process-single-video"]

    assert source.startswith(".github/workflows-deploy/")
    assert name == "process-single-video.yml"


def test_install_refuses_to_overwrite_a_drifted_active_workflow(tmp_path, monkeypatch, capsys):
    targets = prepare(tmp_path, monkeypatch)
    active = targets / "process-single-video.yml"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text("name: newer active workflow\n", encoding="utf-8")

    exit_code = install_workflow.main(["process-single-video"])

    assert exit_code == 1
    assert active.read_text(encoding="utf-8") == "name: newer active workflow\n"
    captured = capsys.readouterr()
    assert "differs from the tracked source" in captured.err
    assert "--force" in captured.err


def test_force_overwrites_a_drifted_active_workflow(tmp_path, monkeypatch):
    targets = prepare(tmp_path, monkeypatch)
    active = targets / "process-single-video.yml"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text("name: newer active workflow\n", encoding="utf-8")

    assert install_workflow.main(["process-single-video", "--force"]) == 0
    assert active.read_text(encoding="utf-8") == "name: process-single-video.yml\n"


def test_install_reports_a_missing_tracked_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install_workflow, "ROOT", tmp_path)

    assert install_workflow.main(["process-single-video"]) == 1
    assert "tracked workflow source is missing" in capsys.readouterr().err
