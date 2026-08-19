---
type: Persistence Guide
title: Checkpointing and Thread Resume
description: Sync and async LangGraph checkpointer factories, backend selection, lifecycle cleanup, and the relationship between thread IDs and resumable state.
tags: [checkpointing, persistence, threads]
openwiki:
  roles: [architecture, operations]
  source_paths: [backend/agents/checkpointer/provider.py, backend/agents/checkpointer/async_provider.py, backend/config/checkpointer_config.py]
  symbols: [get_checkpointer, checkpointer_context, make_checkpointer, reset_checkpointer]
  invariants: [A thread ID preserves conversation state only when an agent has a checkpointer.]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q]
---

# Checkpointing and Thread Resume

Checkpointing persists LangGraph graph state and is separate from [memory](../state/memory.md) and [session search](../state/session-search.md). A `thread_id` is the lookup key, but without a checkpointer repeated calls remain conversationally stateless.

The sync factory in `provider.py` supports memory, SQLite, and PostgreSQL. `get_checkpointer` creates a process singleton, defaults to `InMemorySaver` when no config exists, and keeps connection context alive until `reset_checkpointer`. `checkpointer_context` creates a fresh resource for a bounded block. The async `make_checkpointer` is an async context manager intended for long-lived servers and the CLI; it opens and closes SQLite or PostgreSQL resources with the caller's lifespan.

SQLite paths pass through `runtime/store/_sqlite_utils.py`, which resolves the connection string and creates parent directories before setup. PostgreSQL requires a connection string and optional dependencies. Do not log credentials or persist connection values in this wiki.

The CLI enters `make_checkpointer` before compiling the agent and exits it after the interactive loop. The [embedded client](embedded-client.md) accepts an injected checkpointer and otherwise attempts the package factory during lazy creation. Its thread list/read APIs enumerate checkpoint records, so shipped correctness requires a real checkpointer implementation, not only type-correct client methods.

When adding a backend, update sync and async factories, configuration validation, setup/cleanup behavior, dependency guidance, and tests for missing dependency, missing connection details, singleton reset, and context cleanup. The current core suite has no dedicated checkpointer test file; add one for behavioral changes and retain the smoke import check.
