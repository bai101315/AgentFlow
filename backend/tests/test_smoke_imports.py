from __future__ import annotations

import importlib


def test_core_modules_import():
    modules = [
        "agents.lead_agent.agent",
        "agents.lead_agent.prompt",
        "agents.middlewares.background_review_middleware",
        "agents.review_agent.runtime",
        "config.app_config",
        "config.self_improvement_config",
        "session_search.store",
        "skill.events",
        "skill.manager",
        "skill.usage",
        "tools.builtins.skill_tools",
        "tools.skill_manage_tool",
        "tools.tools",
    ]

    for module in modules:
        importlib.import_module(module)
