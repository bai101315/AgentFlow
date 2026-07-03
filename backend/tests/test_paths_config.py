from __future__ import annotations

from pathlib import Path

from config import paths as paths_module


def test_agentflow_home_priority(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENTFLOW_HOME", raising=False)
    monkeypatch.delenv("DEER_FLOW_HOME", raising=False)

    default_base = paths_module.Paths().base_dir
    assert default_base.name == ".agentflow"

    legacy_home = tmp_path / "legacy-home"
    monkeypatch.setenv("DEER_FLOW_HOME", str(legacy_home))
    assert paths_module.Paths().base_dir == legacy_home.resolve()

    agentflow_home = tmp_path / "agentflow-home"
    monkeypatch.setenv("AGENTFLOW_HOME", str(agentflow_home))
    assert paths_module.Paths().base_dir == agentflow_home.resolve()
