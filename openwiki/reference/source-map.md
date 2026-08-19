---
type: Source Reference
title: Source and Symbol Map
description: Compact map from AgentFlow concepts to canonical implementation symbols, integration points, and focused tests.
tags: [source-map, symbols, navigation]
openwiki:
  roles: [repository]
  source_paths: [main.py, backend]
---

# Source and Symbol Map

Use this as a locator after choosing a concept from the [quickstart](../quickstart.md). Canonical behavior and invariants remain on the linked concept pages.

| Concept | Public or owning API | Implementation | Focused tests |
|---|---|---|---|
| [Lead runtime](../runtime/lead-agent.md) | `make_lead_agent`, `ThreadState` | `backend/agents/lead_agent/agent.py`, `backend/agents/thread_state.py` | `test_smoke_imports.py` plus owning middleware suite |
| [Embedded client](../runtime/embedded-client.md) | `DeerFlowClient`, `StreamEvent` | `backend/client.py` | consumer coverage gap; smoke imports |
| [Configuration](../architecture/configuration.md) | `AppConfig`, `get_app_config`, `Paths` | `backend/config/app_config.py`, `backend/config/paths.py` | `test_config_policy.py`, `test_paths_config.py` |
| [Checkpointing](../runtime/checkpointing.md) | `get_checkpointer`, `make_checkpointer` | `backend/agents/checkpointer` | dedicated behavioral coverage gap |
| [Tools](../tools/registry-and-governance.md) | `get_available_tools` | `backend/tools/tools.py` | `test_smoke_imports.py`, tool-specific suites |
| [MCP/deferred tools](../tools/mcp-and-extensions.md) | `initialize_mcp_tools`, `tool_search` | `backend/deer_flow_mcp/cache.py`, `backend/tools/builtins/tool_search.py` | dedicated cache/registry coverage gap |
| [Models](../tools/models.md) | `create_chat_model` | `backend/models/factory.py` | smoke imports; add provider-normalization tests |
| [Memory](../state/memory.md) | `MemoryMiddleware`, `MemoryUpdateQueue` | `backend/agents/memory`, `backend/agents/middlewares/memory_middleware.py` | `test_memory_middleware.py` |
| [Session search](../state/session-search.md) | `SessionSearchStore`, `search_sessions` | `backend/session_search/store.py` | `test_session_search_core.py` |
| [Observability](../state/observability.md) | `ObservabilityRecorder`, `ObservabilityStore` | `backend/observability` | `test_observability_core.py` |
| [Skill discovery](../skills/library.md) | `load_skills`, `skills_list`, `skill_view` | `backend/skill/loader.py`, `backend/tools/builtins/skill_tools.py` | `test_skill_view_tools.py`, `test_paths_config.py` |
| [Skill writes](../skills/governed-writes.md) | `skill_manage` | `backend/tools/skill_manage_tool.py`, `backend/skill/manager.py` | `test_skill_manage_core.py`, `test_skill_security_scanner.py` |
| [Background review](../skills/background-review.md) | `ReviewRequest`, `run_review` | `backend/agents/review_agent/runtime.py` | `test_review_runtime.py`, `test_background_review_trigger.py` |
| [Curator](../skills/curator-lifecycle.md) | `run_curator`, `apply_automatic_transitions` | `backend/skill/curator.py` | `test_curator_lifecycle.py`, `test_curator_middleware.py` |
| [Subagents](../runtime/subagents.md) | `task_tool`, `SubagentExecutor` | `backend/tools/builtins/task_tool.py`, `backend/subagents` | `test_subagents_registry.py` |
| [CLI](../operations/cli-and-workflows.md) | `main` | `main.py` | orchestration coverage gap; core suite |
