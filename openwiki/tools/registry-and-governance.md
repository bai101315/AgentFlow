---
type: Tooling Guide
title: Tool Registry and Governance
description: How AgentFlow assembles configured, built-in, MCP, skill, session, and subagent tools while enforcing group and host-bash policy.
tags: [tools, governance, registry]
openwiki:
  roles: [architecture, integration]
  source_paths: [backend/tools/tools.py, backend/tools/__init__.py, backend/reflection]
  symbols: [get_available_tools, BUILTIN_TOOLS, SUBAGENT_TOOLS]
  test_paths: [backend/tests/test_smoke_imports.py]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q]
---

# Tool Registry and Governance

`get_available_tools` in `backend/tools/tools.py` is the canonical assembly point. It filters configured `ToolConfig` entries by requested groups, removes host-bash surfaces when sandbox policy disallows them, resolves implementation strings through reflection, and combines them with built-ins and cached MCP tools.

Always-available read surfaces include presentation, clarification, `skills_list`, and `skill_view`. `skill_manage` requires `skill_evolution.enabled`; `session_search` requires its feature flag; `task` requires the runtime `subagent_enabled` flag. MCP inclusion is separately controlled by `include_mcp`. See [built-in tools](built-ins.md), [governed skill writes](../skills/governed-writes.md), and [subagents](../runtime/subagents.md).

Every registry call resets the current deferred registry before loading extensions. When MCP and `tool_search` are enabled, cached MCP tools are registered as deferred and `tool_search` is added; [middleware](../runtime/middleware.md) hides their schemas until promotion. Although the final list still contains MCP tool objects, model binding filters deferred names.

## Adding a Tool

For a configured tool, add its `ToolConfig` example and ensure the reflection target resolves to `BaseTool`. For a built-in, implement the tool, export it from the appropriate built-in/package initializer, register or conditionally append it in `get_available_tools`, and verify the consumer import path used by the lead agent. Add feature/config gating and host-risk classification before exposing it.

Internal correctness is not shipped-surface correctness: test direct behavior, registry inclusion/exclusion, feature-off behavior, group filtering, and the import used by `from tools import get_available_tools`. Use the smoke import check first; add a focused registry test when changing composition. Run the full core suite only when the tool also changes middleware, persistence, or skill governance.
