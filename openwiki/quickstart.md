---
type: Repository Guide
title: AgentFlow Wiki Quickstart
description: Entry point and change router for AgentFlow, a local LangGraph agent runtime with governed tools, persistent conversation state, recall, observability, and skill evolution.
tags: [agentflow, repository, navigation]
openwiki:
  roles: [repository]
  source_paths: [README.md, main.py, backend/client.py]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests -q]
---

# AgentFlow Wiki Quickstart

AgentFlow is a local Python agent runtime with two primary entrypoints: the interactive CLI in `main.py` and the embedded `DeerFlowClient` in `backend/client.py`. Both compose LangChain/LangGraph agents from configured models, governed tools, middleware, `ThreadState`, and an optional checkpointer. The repository also owns durable memory, searchable session recall, subagent delegation, local observability, and governed skill evolution.

## Concept Map

- [Architecture overview](architecture/overview.md) explains the end-to-end turn and component boundaries.
- [Configuration](architecture/configuration.md) covers `AppConfig`, model profiles, extensions, reload behavior, and runtime paths.
- [Lead agent](runtime/lead-agent.md), [middleware](runtime/middleware.md), [checkpointing](runtime/checkpointing.md), [embedded client](runtime/embedded-client.md), and [subagents](runtime/subagents.md) are the runtime path.
- [Tool registry](tools/registry-and-governance.md), [built-ins](tools/built-ins.md), [MCP extensions](tools/mcp-and-extensions.md), and [models](tools/models.md) own the callable surface.
- [Memory](state/memory.md), [session search](state/session-search.md), [sessions and files](state/sessions-and-files.md), and [observability](state/observability.md) own local state.
- [Skill library](skills/library.md), [governed writes](skills/governed-writes.md), [background review](skills/background-review.md), and [curator lifecycle](skills/curator-lifecycle.md) describe self-improvement.
- [CLI workflows](operations/cli-and-workflows.md), [security boundaries](operations/security-and-boundaries.md), and [testing](operations/testing.md) cover operation and validation. [Source map](reference/source-map.md) is a compact symbol index.

## Task Routing

| Change area or intent | Wiki page | Exact source entry points | Important symbols or types | Focused tests | Minimal validation command |
|---|---|---|---|---|---|
| Change agent construction or middleware order | [Lead agent](runtime/lead-agent.md) | `backend/agents/lead_agent/agent.py`, `backend/agents/thread_state.py` | `make_lead_agent`, `_build_middlewares`, `ThreadState` | `backend/tests/test_smoke_imports.py`, affected middleware test | `PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q` |
| Change configuration or paths | [Configuration](architecture/configuration.md) | `backend/config/app_config.py`, `backend/config/paths.py`, `config.example.yaml` | `AppConfig`, `get_app_config`, `Paths` | `test_config_policy.py`, `test_paths_config.py` | `PYTHONPATH=backend python -m pytest backend/tests/test_config_policy.py backend/tests/test_paths_config.py -q` |
| Change the embedded API or stream events | [Embedded client](runtime/embedded-client.md) | `backend/client.py` | `DeerFlowClient`, `StreamEvent` | smoke imports plus new client-focused test | `PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q` |
| Add or gate a tool | [Tool registry](tools/registry-and-governance.md) | `backend/tools/tools.py`, `backend/tools/__init__.py` | `get_available_tools`, `BUILTIN_TOOLS` | `test_smoke_imports.py`, relevant tool test | `PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q` |
| Change MCP or deferred discovery | [MCP extensions](tools/mcp-and-extensions.md) | `backend/deer_flow_mcp/cache.py`, `backend/tools/builtins/tool_search.py` | `initialize_mcp_tools`, `DeferredToolRegistry`, `tool_search` | add focused cache/registry tests | `PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q` |
| Change memory extraction or persistence | [Memory](state/memory.md) | `backend/agents/middlewares/memory_middleware.py`, `backend/agents/memory/queue.py`, `backend/agents/memory/storage.py` | `MemoryMiddleware`, `MemoryUpdateQueue`, `FileMemoryStorage` | `test_memory_middleware.py` | `PYTHONPATH=backend python -m pytest backend/tests/test_memory_middleware.py -q` |
| Change cross-session recall | [Session search](state/session-search.md) | `backend/session_search/store.py`, `backend/tools/builtins/session_search_tool.py` | `SessionSearchStore`, `search_sessions` | `test_session_search_core.py` | `PYTHONPATH=backend python -m pytest backend/tests/test_session_search_core.py -q` |
| Change skill write policy | [Governed writes](skills/governed-writes.md) | `backend/tools/skill_manage_tool.py`, `backend/skill/manager.py` | `_skill_manage_impl`, `_enforce_write_policy` | `test_skill_manage_core.py`, `test_skill_security_scanner.py` | `PYTHONPATH=backend python -m pytest backend/tests/test_skill_manage_core.py backend/tests/test_skill_security_scanner.py -q` |
| Change review or curator automation | [Background review](skills/background-review.md), [curator](skills/curator-lifecycle.md) | `backend/agents/review_agent/runtime.py`, `backend/skill/curator.py` | `run_review`, `ReviewScheduler`, `run_curator` | `test_review_runtime.py`, `test_curator_lifecycle.py` | `PYTHONPATH=backend python -m pytest backend/tests/test_review_runtime.py backend/tests/test_curator_lifecycle.py -q` |
| Change local tracing and summaries | [Observability](state/observability.md) | `backend/observability/recorder.py`, `backend/observability/store.py` | `ObservabilityRecorder`, `ObservabilityStore` | `test_observability_core.py` | `PYTHONPATH=backend python -m pytest backend/tests/test_observability_core.py -q` |
| Change delegation | [Subagents](runtime/subagents.md) | `backend/tools/builtins/task_tool.py`, `backend/subagents/executor.py` | `task_tool`, `SubagentExecutor`, `SubagentStatus` | `test_subagents_registry.py` | `PYTHONPATH=backend python -m pytest backend/tests/test_subagents_registry.py -q` |

Commands assume a synced Python 3.12 environment. Use the full core suite only when a change crosses several systems: `PYTHONPATH=backend python -m pytest backend/tests -q`.

## Backlog

- Git change-range attribution is evidence-blocked because shell Git commands are restricted while `.openwikiignore` is active; the recorded update at `f8fda4f485d5d3e5757b1fe5d1d97aa4d20e3718` was interrupted, so this wiki was rebuilt from current source and tests.
- `backend/client.py` has a large public surface but no dedicated client test module in the inspected core suite; add consumer-level coverage before changing stream serialization or thread APIs.
