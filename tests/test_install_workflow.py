import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "monitoring" / "install_workflow.py"
SPEC = importlib.util.spec_from_file_location("install_workflow", SCRIPT)
install_workflow = importlib.util.module_from_spec(SPEC)
sys.modules["install_workflow"] = install_workflow
SPEC.loader.exec_module(install_workflow)


def test_install_copies_both_tracked_workflow_sources(tmp_path, monkeypatch):
    here = tmp_path / "scripts" / "monitoring"
    here.mkdir(parents=True)
    for name in install_workflow.WORKFLOWS.values():
        (here / name).write_text(f"name: {name}\n", encoding="utf-8")
    monkeypatch.setattr(install_workflow, "HERE", here)
    monkeypatch.setattr(install_workflow, "ROOT", tmp_path)

    install_workflow.install("proxy-scan")
    target = tmp_path / ".github" / "workflows" / "proxy-scan.yml"
    assert target.read_text(encoding="utf-8") == "name: proxy-scan.yml\n"

    install_workflow.main(["all"])
    assert (tmp_path / ".github" / "workflows" / "monitor-whitehouse.yml").is_file()
