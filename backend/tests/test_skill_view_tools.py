from __future__ import annotations

import json
from types import SimpleNamespace

from config.app_config import set_app_config
from tools.builtins.skill_tools import skill_view_tool, skills_list_tool


def _write_skill(root, category, name, description="Demo workflow"):
    skill_dir = root / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def _install_skills_root(tmp_path):
    skills_root = tmp_path / "skills"
    set_app_config(
        SimpleNamespace(
            skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills"),
            skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None, require_background_provenance=True),
        )
    )
    return skills_root


def _stub_access_recorder(monkeypatch):
    recorded: list[tuple[str, str]] = []
    import skill.usage as usage

    monkeypatch.setattr(usage, "record_skill_access", lambda name, access: recorded.append((name, access)))
    return recorded


def test_skills_list_returns_metadata_without_content(monkeypatch, tmp_path):
    """§Phase 2: the index must never carry skill bodies."""
    skills_root = _install_skills_root(tmp_path)
    _write_skill(skills_root, "public", "bootstrap", "Set up the project")
    _write_skill(skills_root, "custom", "my-skill", "Do the thing")

    payload = json.loads(skills_list_tool.invoke({}))

    assert payload["count"] == 2
    names = {entry["name"] for entry in payload["skills"]}
    assert names == {"bootstrap", "my-skill"}
    for entry in payload["skills"]:
        assert "content" not in entry
        assert entry["location"].startswith("/mnt/skills/")
    assert "# my-skill" not in json.dumps(payload)


def test_skills_list_filters_by_category(monkeypatch, tmp_path):
    skills_root = _install_skills_root(tmp_path)
    _write_skill(skills_root, "public", "bootstrap")
    _write_skill(skills_root, "custom", "my-skill")

    payload = json.loads(skills_list_tool.invoke({"category": "custom"}))

    assert [entry["name"] for entry in payload["skills"]] == ["my-skill"]


def test_skill_view_returns_content_and_records_view(monkeypatch, tmp_path):
    skills_root = _install_skills_root(tmp_path)
    skill_dir = _write_skill(skills_root, "custom", "my-skill")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "detail.md").write_text("detail", encoding="utf-8")
    recorded = _stub_access_recorder(monkeypatch)

    payload = json.loads(skill_view_tool.invoke({"name": "my-skill"}))

    assert "# my-skill" in payload["content"]
    assert payload["support_files"] == ["/mnt/skills/custom/my-skill/references/detail.md"]
    assert recorded == [("my-skill", "view")]


def test_skill_view_unknown_skill_records_nothing(monkeypatch, tmp_path):
    _install_skills_root(tmp_path)
    recorded = _stub_access_recorder(monkeypatch)

    payload = json.loads(skill_view_tool.invoke({"name": "nope"}))

    assert "error" in payload
    assert recorded == []


def test_skill_view_survives_usage_recording_failure(monkeypatch, tmp_path):
    """§3.4: bookkeeping failures must not break the read path."""
    skills_root = _install_skills_root(tmp_path)
    _write_skill(skills_root, "custom", "my-skill")

    import skill.usage as usage

    def boom(name, access):
        raise RuntimeError("usage store unavailable")

    monkeypatch.setattr(usage, "record_skill_access", boom)

    payload = json.loads(skill_view_tool.invoke({"name": "my-skill"}))

    assert "# my-skill" in payload["content"]
