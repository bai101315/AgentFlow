from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_registry(
    monkeypatch,
    *,
    timeout_seconds: int | None = None,
    max_turns: int | None = None,
    global_timeout_seconds: int = 300,
    global_max_turns: int | None = 100,
):
    subagents_pkg = types.ModuleType("subagents")
    subagents_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "subagents", subagents_pkg)

    config_module = _load_module(
        "subagents.config",
        BACKEND_DIR / "subagents" / "config.py",
    )
    coding_module = _load_module(
        "subagents.builtins.coding_agent",
        BACKEND_DIR / "subagents" / "builtins" / "coding_agent.py",
    )

    builtins_module = types.ModuleType("subagents.builtins")
    builtins_module.CODING_AGENT_CONFIG = coding_module.CODING_AGENT_CONFIG
    builtins_module.BUILTIN_SUBAGENTS = {"code": coding_module.CODING_AGENT_CONFIG}
    monkeypatch.setitem(sys.modules, "subagents.builtins", builtins_module)

    sandbox_pkg = types.ModuleType("sandbox")
    sandbox_pkg.__path__ = []
    security_module = types.ModuleType("sandbox.security")
    security_module.is_host_bash_allowed = lambda: True
    monkeypatch.setitem(sys.modules, "sandbox", sandbox_pkg)
    monkeypatch.setitem(sys.modules, "sandbox.security", security_module)

    def get_timeout_for(_name, builtin_default):
        if timeout_seconds is not None:
            return timeout_seconds
        if builtin_default is not None:
            return builtin_default
        return global_timeout_seconds

    def get_max_turns_for(_name, builtin_default):
        if max_turns is not None:
            return max_turns
        if builtin_default is not None:
            return builtin_default
        if global_max_turns is not None:
            return global_max_turns
        return 50

    app_subagents_config = SimpleNamespace(
        get_timeout_for=get_timeout_for,
        get_max_turns_for=get_max_turns_for,
    )
    app_config_pkg = types.ModuleType("config")
    app_config_pkg.__path__ = []
    subagents_config_module = types.ModuleType("config.subagents_config")
    subagents_config_module.get_subagents_app_config = lambda: app_subagents_config
    monkeypatch.setitem(sys.modules, "config", app_config_pkg)
    monkeypatch.setitem(sys.modules, "config.subagents_config", subagents_config_module)

    registry_module = _load_module(
        "subagents.registry",
        BACKEND_DIR / "subagents" / "registry.py",
    )
    return registry_module, coding_module.CODING_AGENT_CONFIG, config_module.SubagentConfig


def test_code_subagent_aliases_resolve_to_coding_agent_config(monkeypatch):
    registry, coding_config, _subagent_config_type = _load_registry(monkeypatch)

    assert registry.get_subagent_config("code") is coding_config
    assert registry.get_subagent_config("coding_agent") is coding_config
    assert registry.get_subagent_config("coding-agent") is coding_config
    assert registry.get_subagent_config("code-agent") is coding_config
    assert registry.get_subagent_config("coding") is coding_config
    assert registry.get_subagent_config("general-purpose") is None


def test_code_subagent_alias_keeps_builtin_max_turns_over_global_default(monkeypatch):
    registry, coding_config, _subagent_config_type = _load_registry(
        monkeypatch,
        global_max_turns=100,
    )

    config = registry.get_subagent_config("coding_agent")

    assert config is coding_config
    assert config.max_turns == 200


def test_code_subagent_alias_keeps_builtin_timeout_over_global_default(monkeypatch):
    registry, coding_config, _subagent_config_type = _load_registry(
        monkeypatch,
        global_timeout_seconds=300,
    )

    config = registry.get_subagent_config("coding-agent")

    assert config is coding_config
    assert config.timeout_seconds == 900


def test_code_subagent_alias_uses_canonical_per_agent_overrides(monkeypatch):
    registry, coding_config, subagent_config_type = _load_registry(
        monkeypatch,
        timeout_seconds=600,
        max_turns=150,
    )

    config = registry.get_subagent_config("coding-agent")

    assert isinstance(config, subagent_config_type)
    assert config is not coding_config
    assert config.name == "code"
    assert config.timeout_seconds == 600
    assert config.max_turns == 150
    assert coding_config.timeout_seconds == 900
    assert coding_config.max_turns == 200
