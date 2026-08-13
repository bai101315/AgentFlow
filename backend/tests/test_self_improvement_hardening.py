from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from agents.memory.queue import MemoryUpdateQueue
from agents.memory.updater import MemoryUpdater, _secret_rule
from config.app_config import set_app_config
from skill.events import read_events
from skill.security_scanner import ScanResult
from tools.skill_manage_tool import build_background_skill_manage_tool


def _skill_content(name: str) -> str:
    return f"---\nname: {name}\ndescription: A reusable workflow\n---\n\n# {name}\n"


def _setup_skills(monkeypatch, tmp_path):
    root = tmp_path / "skills"
    set_app_config(SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: root, container_path="/mnt/skills"),
        skill_evolution=SimpleNamespace(enabled=True, require_background_provenance=True, moderation_model_name=None),
    ))
    import tools.skill_manage_tool as module

    async def scan(*args, **kwargs):
        return ScanResult(decision="allow", reason="ok")

    async def refresh():
        return None

    monkeypatch.setattr(module, "scan_skill_content", scan)
    monkeypatch.setattr(module, "refresh_skills_system_prompt_cache_async", refresh)
    monkeypatch.setenv("AGENTFLOW_HOME", str(tmp_path / "home"))
    import config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", None, raising=False)
    return root


def test_background_action_limit_counts_only_successful_writes(monkeypatch, tmp_path):
    root = _setup_skills(monkeypatch, tmp_path)
    results = []
    tool = build_background_skill_manage_tool(
        origin="background_review",
        execution_context="background_review",
        thread_id="review-1",
        parent_thread_id="parent-1",
        max_actions=1,
        on_applied=results.append,
    )

    asyncio.run(tool.ainvoke({"action": "create", "name": "limited-skill", "content": _skill_content("limited-skill")}))
    with pytest.raises(PermissionError, match="action limit"):
        asyncio.run(tool.ainvoke({"action": "patch", "name": "limited-skill", "find": "missing", "replace": "new"}))

    assert len(results) == 1
    assert (root / "custom" / "limited-skill" / "SKILL.md").exists()


def test_memory_secret_input_is_rejected_without_content(monkeypatch, tmp_path, real_events):
    _setup_skills(monkeypatch, tmp_path)
    set_app_config(SimpleNamespace(
        memory=SimpleNamespace(enabled=True, model_name=None, fact_confidence_threshold=0.7, max_facts=20),
    ))
    updater = MemoryUpdater()
    assert not updater.update_memory([SimpleNamespace(type="human", content="api_key=supersecretvalue")], thread_id="thread-secret")
    assert any(event["event"] == "memory_rejected" for event in read_events())


def test_provider_token_regex_matches_real_keys_but_not_skill_identifiers():
    assert _secret_rule("sk-proj-abcdefghijklmnopqrstuvwxyz") == "provider_token"
    assert _secret_rule("sk-ant-api03-abcdefghijklmnopqrstuvwxyz") == "provider_token"
    assert _secret_rule("github_pat_abcdefghijklmnopqrstuvwxyz") == "provider_token"
    assert _secret_rule("ghp_abcdefghijkl") == "provider_token"
    assert _secret_rule("用 skill_manage_tool 固化流程") is None
    assert _secret_rule("调用 skill_view 查看 data-analysis") is None
    assert _secret_rule("skill 这个词不该被误伤") is None


def test_memory_queue_discard_pending_clears_timer_and_queue(monkeypatch):
    set_app_config(SimpleNamespace(memory=SimpleNamespace(enabled=True, debounce_seconds=60)))
    queue = MemoryUpdateQueue()
    queue.add("thread-1", [SimpleNamespace(type="human", content="hello")])
    assert queue.discard_pending() == 1
    assert queue.discard_pending() == 0
