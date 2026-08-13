from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from agents.review_agent.runtime import ReviewRequest, ReviewResult, build_review_tools, run_review
from config.app_config import set_app_config
from skill.security_scanner import ScanResult


def _skill_content(name: str, description: str = "Demo workflow") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def _install_skills_root(monkeypatch, tmp_path):
    skills_root = tmp_path / "skills"
    set_app_config(
        SimpleNamespace(
            skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills"),
            skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None, require_background_provenance=True),
            background_review=SimpleNamespace(notifications="off"),
        )
    )

    import tools.skill_manage_tool as skill_manage_module

    async def scan(*args, **kwargs):
        return ScanResult(decision="allow", reason="ok")

    async def refresh():
        return None

    monkeypatch.setattr(skill_manage_module, "scan_skill_content", scan)
    monkeypatch.setattr(skill_manage_module, "refresh_skills_system_prompt_cache_async", refresh)
    monkeypatch.setenv("AGENTFLOW_HOME", str(tmp_path / "home"))
    import config.paths as paths_module

    monkeypatch.setattr(paths_module, "_paths", None, raising=False)
    return skills_root


def _request(**overrides) -> ReviewRequest:
    kwargs = {
        "transcript": "user: do the thing\n\nassistant: done",
        "parent_thread_id": "parent-thread",
        "max_actions": 3,
        "timeout_seconds": 30,
    }
    kwargs.update(overrides)
    return ReviewRequest(**kwargs)


def test_review_request_is_immutable():
    """§6.4 forbids passing review context through shared mutable state."""
    request = _request()
    with pytest.raises(Exception):
        request.transcript = "tampered"


def test_review_thread_id_differs_from_parent():
    request = _request()
    assert request.review_thread_id != request.parent_thread_id
    assert request.review_thread_id.startswith("review-")


def test_review_tools_expose_only_skill_surface(monkeypatch, tmp_path):
    """§6.2/§14.2: the reviewer must not hold ordinary business tools."""
    _install_skills_root(monkeypatch, tmp_path)

    tools = build_review_tools(_request(), lambda result: None)

    assert {tool.name for tool in tools} == {"skills_list", "skill_view", "skill_manage"}


def test_review_skill_manage_schema_excludes_destructive_actions(monkeypatch, tmp_path):
    """delete/remove_file must not even be advertised to the reviewer (§6.2)."""
    _install_skills_root(monkeypatch, tmp_path)

    tools = build_review_tools(_request(), lambda result: None)
    manage = next(tool for tool in tools if tool.name == "skill_manage")

    assert "Deleting skills is not permitted" in manage.description


def test_review_writes_carry_background_provenance(monkeypatch, tmp_path):
    """A skill written by the reviewer is background-owned and curator-managed."""
    skills_root = _install_skills_root(monkeypatch, tmp_path)
    request = _request()

    applied: list[str] = []
    tools = build_review_tools(request, applied.append)
    manage = next(tool for tool in tools if tool.name == "skill_manage")

    asyncio.run(
        manage.ainvoke({"action": "create", "name": "learned-skill", "content": _skill_content("learned-skill")})
    )

    assert applied and "Created custom skill" in applied[0]

    usage = json.loads((skills_root / "custom" / ".usage.json").read_text(encoding="utf-8"))
    record = usage["skills"]["learned-skill"]
    assert record["created_by"] == "background_review"
    assert record["managed_by_curator"] is True

    history = [
        json.loads(line)
        for line in (skills_root / "custom" / ".history" / "learned-skill.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert history[0]["origin"] == "background_review"
    assert history[0]["parent_thread_id"] == "parent-thread"
    assert history[0]["thread_id"] == request.review_thread_id


def test_review_cannot_delete_even_through_its_own_tool(monkeypatch, tmp_path):
    skills_root = _install_skills_root(monkeypatch, tmp_path)
    request = _request()
    tools = build_review_tools(request, lambda result: None)
    manage = next(tool for tool in tools if tool.name == "skill_manage")

    asyncio.run(
        manage.ainvoke({"action": "create", "name": "learned-skill", "content": _skill_content("learned-skill")})
    )
    with pytest.raises(PermissionError):
        asyncio.run(manage.ainvoke({"action": "delete", "name": "learned-skill"}))

    assert (skills_root / "custom" / "learned-skill" / "SKILL.md").exists()


def test_run_review_returns_failure_instead_of_raising(monkeypatch, tmp_path):
    """§3.4: a broken reviewer must never propagate into the foreground."""
    _install_skills_root(monkeypatch, tmp_path)

    import agents.review_agent.runtime as runtime_module

    def boom(request, tools):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(runtime_module, "_build_review_agent", boom)

    result = asyncio.run(run_review(_request()))

    assert result.status == "failed"
    assert result.applied == []
    assert "model unavailable" in (result.error or "")


def test_run_review_times_out_without_raising(monkeypatch, tmp_path):
    _install_skills_root(monkeypatch, tmp_path)

    import agents.review_agent.runtime as runtime_module

    class SlowAgent:
        async def ainvoke(self, *args, **kwargs):
            await asyncio.sleep(5)

    monkeypatch.setattr(runtime_module, "_build_review_agent", lambda request, tools: SlowAgent())

    result = asyncio.run(run_review(_request(timeout_seconds=0.05)))

    assert result.status == "timeout"


def test_run_review_returns_only_final_summary(monkeypatch, tmp_path):
    """§6.4: the reviewer returns an action summary, not its internal messages."""
    _install_skills_root(monkeypatch, tmp_path)

    import agents.review_agent.runtime as runtime_module

    class FakeAgent:
        async def ainvoke(self, payload, config=None):
            return {
                "messages": [
                    SimpleNamespace(content="internal reasoning that must not leak", type="ai"),
                    SimpleNamespace(content="noop", type="ai"),
                ]
            }

    monkeypatch.setattr(runtime_module, "_build_review_agent", lambda request, tools: FakeAgent())

    result = asyncio.run(run_review(_request()))

    assert result.status == "completed"
    assert result.summary == "noop"
    assert not isinstance(result.applied, str)


def test_review_events_record_thread_lineage(monkeypatch, tmp_path, real_events):
    """§3.3: every automated change must be traceable to its parent thread."""
    _install_skills_root(monkeypatch, tmp_path)

    import agents.review_agent.runtime as runtime_module

    class FakeAgent:
        async def ainvoke(self, payload, config=None):
            return {"messages": [SimpleNamespace(content="noop", type="ai")]}

    monkeypatch.setattr(runtime_module, "_build_review_agent", lambda request, tools: FakeAgent())

    request = _request()
    asyncio.run(run_review(request))

    from skill.events import read_events

    events = read_events()
    names = [event["event"] for event in events]
    assert names == ["review_started", "review_completed"]
    for event in events:
        assert event["parent_thread_id"] == "parent-thread"
        assert event["review_thread_id"] == request.review_thread_id


def test_review_result_defaults_are_independent():
    first = ReviewResult(status="completed", review_thread_id="a")
    second = ReviewResult(status="completed", review_thread_id="b")
    first.applied.append("x")
    assert second.applied == []
