from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from config.app_config import set_app_config
from skill.security_scanner import scan_skill_content


def test_security_scan_uses_foreground_default_model_when_moderation_model_is_unset(monkeypatch):
    set_app_config(
        SimpleNamespace(
            skill_evolution=SimpleNamespace(moderation_model_name=None),
            skills=SimpleNamespace(guard_agent_created=True),
            models=[SimpleNamespace(name="qwen3.5-flash"), SimpleNamespace(name="deepseek-v4")],
            get_model_config=lambda name: SimpleNamespace(name=name) if name == "deepseek-v4" else None,
        )
    )
    selected: list[str | None] = []

    class FakeModel:
        async def ainvoke(self, messages, config=None):
            return SimpleNamespace(
                content=json.dumps({"decision": "allow", "reason": "safe"})
            )

    def fake_create_chat_model(*, name=None, thinking_enabled=False):
        selected.append(name)
        return FakeModel()

    import agents.lead_agent.agent as lead_agent_module
    import skill.security_scanner as scanner_module

    monkeypatch.setattr(lead_agent_module, "_resolve_model_name", lambda: "deepseek-v4")
    monkeypatch.setattr(scanner_module, "create_chat_model", fake_create_chat_model)

    result = asyncio.run(scan_skill_content("# Safe workflow"))

    assert result.decision == "allow"
    assert selected == ["deepseek-v4"]


def test_security_scan_honours_explicit_moderation_model(monkeypatch):
    set_app_config(
        SimpleNamespace(
            skill_evolution=SimpleNamespace(moderation_model_name="qwen3.5-flash"),
            skills=SimpleNamespace(guard_agent_created=True),
        )
    )
    selected: list[str | None] = []

    class FakeModel:
        async def ainvoke(self, messages, config=None):
            return SimpleNamespace(
                content=json.dumps({"decision": "allow", "reason": "safe"})
            )

    def fake_create_chat_model(*, name=None, thinking_enabled=False):
        selected.append(name)
        return FakeModel()

    import skill.security_scanner as scanner_module

    monkeypatch.setattr(scanner_module, "create_chat_model", fake_create_chat_model)

    result = asyncio.run(scan_skill_content("# Safe workflow"))

    assert result.decision == "allow"
    assert selected == ["qwen3.5-flash"]


def test_security_scan_disabled_by_default_skips_model_call(monkeypatch):
    # guard_agent_created defaults to False: scan is a no-op, no LLM call.
    set_app_config(
        SimpleNamespace(
            skill_evolution=SimpleNamespace(moderation_model_name=None),
            skills=SimpleNamespace(guard_agent_created=False),
        )
    )
    called = []

    import skill.security_scanner as scanner_module

    def fake_create_chat_model(*, name=None, thinking_enabled=False):
        called.append(name)
        raise AssertionError("model must not be called when guard is disabled")

    monkeypatch.setattr(scanner_module, "create_chat_model", fake_create_chat_model)

    result = asyncio.run(scan_skill_content("anything", executable=True))

    assert result.decision == "allow"
    assert called == []
