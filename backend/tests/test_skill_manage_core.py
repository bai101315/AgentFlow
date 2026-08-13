from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
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


def _install_skills_root(monkeypatch, tmp_path):
    """Point the skill store at a temp dir and stub out scanning/cache refresh."""
    skills_root = tmp_path / "skills"
    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None, require_background_provenance=True),
    )
    set_app_config(config)

    import tools.skill_manage_tool as skill_manage_module

    async def scan(*args, **kwargs):
        return ScanResult(decision="allow", reason="ok")

    async def refresh():
        return None

    monkeypatch.setattr(skill_manage_module, "scan_skill_content", scan)
    monkeypatch.setattr(skill_manage_module, "refresh_skills_system_prompt_cache_async", refresh)
    # Keep event writes inside the temp dir instead of the real AGENTFLOW_HOME.
    monkeypatch.setenv("AGENTFLOW_HOME", str(tmp_path / "home"))
    import config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", None, raising=False)
    return skills_root


def _read_usage(skills_root, name):
    return json.loads((skills_root / "custom" / ".usage.json").read_text(encoding="utf-8"))["skills"][name]


def test_skill_manage_create_and_patch_records_history_and_usage(monkeypatch, tmp_path):
    skills_root = _install_skills_root(monkeypatch, tmp_path)

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
    assert [record["origin"] for record in history] == ["foreground_user", "foreground_user"]

    record = _read_usage(skills_root, "demo-skill")
    assert record["patch_count"] == 1


def test_foreground_created_skill_is_not_curator_managed(monkeypatch, tmp_path):
    """§7.2/§7.3: a user-authored skill must never be enrolled into automatic maintenance."""
    skills_root = _install_skills_root(monkeypatch, tmp_path)

    asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="create",
            name="demo-skill",
            content=_skill_content("demo-skill"),
        )
    )

    record = _read_usage(skills_root, "demo-skill")
    assert record["created_by"] == "foreground_user"
    assert record["managed_by_curator"] is False


def test_background_review_created_skill_is_curator_managed(monkeypatch, tmp_path):
    skills_root = _install_skills_root(monkeypatch, tmp_path)

    asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="create",
            name="bg-skill",
            content=_skill_content("bg-skill"),
            origin="background_review",
            execution_context="background_review",
        )
    )

    record = _read_usage(skills_root, "bg-skill")
    assert record["created_by"] == "background_review"
    assert record["managed_by_curator"] is True


def test_background_patch_cannot_touch_foreground_skill(monkeypatch, tmp_path):
    """A background origin may only modify curator-managed skills (§7.3)."""
    skills_root = _install_skills_root(monkeypatch, tmp_path)

    asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="create",
            name="demo-skill",
            content=_skill_content("demo-skill"),
        )
    )

    with pytest.raises(PermissionError):
        asyncio.run(
            _skill_manage_impl(
                runtime=_runtime(),
                action="patch",
                name="demo-skill",
                find="Demo workflow",
                replace="Hijacked",
                origin="background_review",
                execution_context="background_review",
            )
        )

    skill_file = skills_root / "custom" / "demo-skill" / "SKILL.md"
    assert "Hijacked" not in skill_file.read_text(encoding="utf-8")
    # The rejected write must not leave provenance behind either.
    assert _read_usage(skills_root, "demo-skill")["origins"] == ["foreground_user"]


def test_background_patch_does_not_overwrite_created_by(monkeypatch, tmp_path):
    """created_by is a historical fact; last_origin tracks the latest write (§7.2)."""
    skills_root = _install_skills_root(monkeypatch, tmp_path)

    asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="create",
            name="bg-skill",
            content=_skill_content("bg-skill"),
            origin="background_review",
            execution_context="background_review",
        )
    )
    asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="patch",
            name="bg-skill",
            find="Demo workflow",
            replace="Curated workflow",
            origin="curator",
            execution_context="curator",
        )
    )

    record = _read_usage(skills_root, "bg-skill")
    assert record["created_by"] == "background_review"
    assert record["last_origin"] == "curator"


def test_background_origin_cannot_delete(monkeypatch, tmp_path):
    """§3.5: the most destructive automatic action is archive, never delete."""
    skills_root = _install_skills_root(monkeypatch, tmp_path)

    asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="create",
            name="bg-skill",
            content=_skill_content("bg-skill"),
            origin="background_review",
            execution_context="background_review",
        )
    )

    for action, kwargs in (("delete", {}), ("remove_file", {"path": "references/detail.md"})):
        with pytest.raises(PermissionError):
            asyncio.run(
                _skill_manage_impl(
                    runtime=_runtime(),
                    action=action,
                    name="bg-skill",
                    origin="background_review",
                    execution_context="background_review",
                    **kwargs,
                )
            )

    assert (skills_root / "custom" / "bg-skill" / "SKILL.md").exists()


def test_foreground_skill_delete_is_suspended(monkeypatch, tmp_path):
    """Whole-skill deletion stays disabled while patch/edit remain available."""
    skills_root = _install_skills_root(monkeypatch, tmp_path)

    asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="create",
            name="demo-skill",
            content=_skill_content("demo-skill"),
        )
    )

    with pytest.raises(PermissionError, match="Direct Skill deletion is temporarily disabled"):
        asyncio.run(
            _skill_manage_impl(
                runtime=_runtime(),
                action="delete",
                name="demo-skill",
            )
        )

    assert (skills_root / "custom" / "demo-skill" / "SKILL.md").exists()


def test_background_origin_cannot_modify_pinned_skill(monkeypatch, tmp_path):
    skills_root = _install_skills_root(monkeypatch, tmp_path)

    asyncio.run(
        _skill_manage_impl(
            runtime=_runtime(),
            action="create",
            name="bg-skill",
            content=_skill_content("bg-skill"),
            origin="background_review",
            execution_context="background_review",
        )
    )

    usage_path = skills_root / "custom" / ".usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    usage["skills"]["bg-skill"]["pinned"] = True
    usage_path.write_text(json.dumps(usage), encoding="utf-8")

    with pytest.raises(PermissionError):
        asyncio.run(
            _skill_manage_impl(
                runtime=_runtime(),
                action="patch",
                name="bg-skill",
                find="Demo workflow",
                replace="Changed",
                origin="background_review",
                execution_context="background_review",
            )
        )


def test_background_execution_context_must_declare_background_origin(monkeypatch, tmp_path):
    """require_background_provenance rejects an automated write claiming foreground origin."""
    _install_skills_root(monkeypatch, tmp_path)

    with pytest.raises(PermissionError):
        asyncio.run(
            _skill_manage_impl(
                runtime=_runtime(),
                action="create",
                name="sneaky-skill",
                content=_skill_content("sneaky-skill"),
                origin="foreground_user",
                execution_context="background_review",
            )
        )


def test_unknown_origin_is_rejected(monkeypatch, tmp_path):
    _install_skills_root(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        asyncio.run(
            _skill_manage_impl(
                runtime=_runtime(),
                action="create",
                name="demo-skill",
                content=_skill_content("demo-skill"),
                origin="mystery",
            )
        )
