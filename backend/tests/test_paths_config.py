from __future__ import annotations

from pathlib import Path

from config import paths as paths_module
from config.skills_config import SkillsConfig, _default_repo_root


def test_repo_root_resolves_to_the_directory_holding_backend():
    """A wrong parent count here silently empties the whole skill system.

    ``_default_repo_root`` resolves relative ``skills.path`` values. When it
    pointed too far up, ``./skills`` became ``/home/skills``, ``load_skills()``
    returned nothing, and the agent saw an empty skill index with no error.
    """
    repo_root = _default_repo_root()

    assert (repo_root / "backend").is_dir()
    assert (repo_root / "backend" / "config" / "skills_config.py").is_file()


def test_relative_skills_path_resolves_under_repo_root():
    resolved = SkillsConfig(path="./skills").get_skills_path()

    assert resolved == (_default_repo_root() / "skills").resolve()


def test_configured_skills_path_exists_and_loads_skills():
    """The shipped config must point at a real skills tree."""
    from config import get_app_config
    from skill.loader import load_skills

    skills_path = get_app_config().skills.get_skills_path()
    assert skills_path.is_dir(), f"configured skills path does not exist: {skills_path}"
    assert load_skills(enabled_only=True), "no enabled skills loaded from the configured path"


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
