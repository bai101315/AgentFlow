---
type: State Reference
title: Sessions and Local Files
description: AgentFlow local state ownership under `.agentflow`, thread and agent identity files, path safety, and retention boundaries.
tags: [state, files, sessions]
openwiki:
  roles: [repository, operations]
  source_paths: [backend/config/paths.py, main.py, backend/agents/checkpointer]
  symbols: [Paths, _ensure_agent_thread_id, _rotate_agent_thread]
  test_paths: [backend/tests/test_paths_config.py]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_paths_config.py -q]
---

# Sessions and Local Files

The default state root is `.agentflow`, overridden by `AGENTFLOW_HOME` and then compatibility `DEER_FLOW_HOME`. `Paths` is the canonical path API; avoid reconstructing these locations in new modules.

| State | Owner | Default location |
|---|---|---|
| Global profile and memory | prompt/memory systems | `.agentflow/USER.md`, `.agentflow/memory.json` |
| Custom agent identity | agent config | `.agentflow/agents/<name>/config.yaml`, `SOUL.md`, `memory.json` |
| Agent-to-thread mapping | CLI | `.agentflow/agent_threads.yaml` |
| Last selected agent | CLI | `.agentflow/last_agent.txt` |
| Uploads and outputs | `Paths` | `.agentflow/threads/<thread>/user-data/...` |
| Checkpoints | checkpointer | configured SQLite file, commonly under state root |
| Search index | session search | `.agentflow/session_search.db` by default |
| Observability | observability store | `.agentflow/observability.db` and `.agentflow/observability/...` |
| Skill usage/history/archive | skill system | under configured `skills/custom` |

The CLI maintains one default thread ID per normalized agent. `--new-session` rotates and persists that mapping; ordinary startup resumes it. This mapping chooses a checkpoint key but is not itself the conversation state.

`Paths.thread_dir` validates thread IDs before constructing paths. Virtual file resolution requires `/mnt/user-data` and rejects traversal after canonical resolution. The current `sandbox_work_dir` exposes the repository root for local project work, while uploads and outputs remain thread-specific; this is an important [security boundary](../operations/security-and-boundaries.md).

Do not commit local state, databases, logs, private configuration, or generated artifacts. Changing path precedence or layout requires migration/compatibility analysis and `test_paths_config.py`; changing thread rotation also requires CLI-level tests, which are not present in the inspected focused suite.
