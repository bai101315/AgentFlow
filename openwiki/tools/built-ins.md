---
type: Tool Reference
title: Built-in Tools
description: User-facing built-in AgentFlow tools and the feature gates, state contracts, and canonical pages that own their behavior.
tags: [tools, built-ins, user-surface]
openwiki:
  roles: [repository, workflow]
  source_paths: [backend/tools/builtins, backend/tools/skill_manage_tool.py]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_skill_view_tools.py backend/tests/test_session_search_core.py -q]
---

# Built-in Tools

Built-ins are normal LangChain tools assembled by the [tool registry](registry-and-governance.md). Their modules own schemas and return values; the registry owns exposure.

| Tool | Source | Contract and gate |
|---|---|---|
| `present_file` | `builtins/present_file_tool.py` | Presents a file/artifact from thread state. |
| `ask_clarification` | `builtins/clarification_tool.py` | Requests missing user input through clarification middleware. |
| `skills_list` | `builtins/skill_tools.py` | Returns enabled skill metadata only and is available whenever skills can be loaded. |
| `skill_view` | `builtins/skill_tools.py` | Returns one `SKILL.md` plus support-file paths and records a view signal. |
| `skill_manage` | `tools/skill_manage_tool.py` | Governed custom-skill writes; exposed only when skill evolution is enabled. |
| `session_search` | `builtins/session_search_tool.py` | Search/browse/read/around modes over the session-search store; feature-gated. |
| `task` | `builtins/task_tool.py` | Delegates to a subagent; runtime-gated and prevents nested delegation. |
| `tool_search` | `builtins/tool_search.py` | Returns full schemas for matching deferred tools and promotes them. |
| `setup_agent` | `builtins/setup_agent_tool.py` | Used by bootstrap agent creation. |

The detailed contracts live in [skill library](../skills/library.md), [governed writes](../skills/governed-writes.md), [session search](../state/session-search.md), [subagents](../runtime/subagents.md), and [MCP extensions](mcp-and-extensions.md).

When changing a tool schema, update its docstring because LangChain derives model-facing descriptions from it. Test invalid arguments and return shape as well as successful behavior. Registry inclusion is a separate assertion from direct tool tests.
