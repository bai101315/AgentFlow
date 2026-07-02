from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from config.app_config import set_app_config
from skill.security_scanner import ScanResult
from tools.skill_manage_tool import _skill_manage_impl


def _skill_content(name: str, description: str = "Demo workflow") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def _runtime(thread_id: str = "thread-1"):
    return SimpleNamespace(
        context={"thread_id": thread_id},
        config={"configurable": {"thread_id": thread_id}},
    )


def test_skill_manage_create_and_patch_records_history_and_usage(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    set_app_config(config)

    import tools.skill_manage_tool as skill_manage_module

    async def scan(*args, **kwargs):
        return ScanResult(decision="allow", reason="ok")

    async def refresh():
        return None

    monkeypatch.setattr(skill_manage_module, "scan_skill_content", scan)
    monkeypatch.setattr(skill_manage_module, "refresh_skills_system_prompt_cache_async", refresh)

    create_result = asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="create",
            name="demo-skill",
            content=_skill_content("demo-skill"),
        )
    )
    assert "Created custom skill" in create_result

    patch_result = asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="patch",
            name="demo-skill",
            find="Demo workflow",
            replace="Patched workflow",
            expected_count=1,
        )
    )
    assert "Patched custom skill" in patch_result

    skill_file = skills_root / "custom" / "demo-skill" / "SKILL.md"
    assert "Patched workflow" in skill_file.read_text(encoding="utf-8")

    history_path = skills_root / "custom" / ".history" / "demo-skill.jsonl"
    history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
    assert [record["action"] for record in history] == ["create", "patch"]
    assert [record["origin"] for record in history] == ["foreground", "foreground"]

    usage = json.loads((skills_root / "custom" / ".usage.json").read_text(encoding="utf-8"))
    assert usage["skills"]["demo-skill"]["patch_count"] == 1
