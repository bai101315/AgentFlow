---
type: Configuration Guide
title: Configuration and Runtime Paths
description: Configuration loading, cache and reload behavior, model and feature settings, extension configuration, and local state path resolution.
tags: [configuration, paths, extensions]
openwiki:
  roles: [architecture, operations]
  source_paths: [backend/config/app_config.py, backend/config/extensions_config.py, backend/config/paths.py, config.example.yaml]
  symbols: [AppConfig, ExtensionsConfig, Paths, get_app_config]
  test_paths: [backend/tests/test_config_policy.py, backend/tests/test_paths_config.py]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_config_policy.py backend/tests/test_paths_config.py -q]
---

# Configuration and Runtime Paths

`AppConfig` in `backend/config/app_config.py` is the root typed configuration. It owns model, tool, sandbox, skill, memory, review, curator, session-search, observability, checkpointer, and middleware-related settings. `AppConfig.from_file` reads YAML, checks `config_version`, recursively resolves `$ENV_VAR` strings, refreshes module-specific config holders, then loads `ExtensionsConfig` from its separate JSON file.

## Resolution and Reload

`AppConfig.resolve_config_path` uses an explicit path, then `DEER_FLOW_CONFIG_PATH`, then deterministic repository defaults. `get_app_config` caches the parsed object but reloads when the resolved file or modification time changes. `reload_app_config` forces disk loading; `set_app_config` installs a test/custom singleton. Context-local `push_current_app_config` and `pop_current_app_config` isolate runtime overrides between concurrent execution contexts.

`ExtensionsConfig` owns MCP servers and enabled skill state. It resolves an explicit path, `DEER_FLOW_EXTENSIONS_CONFIG_PATH`, repository defaults, then legacy `mcp_config.json`; absence is valid. Unresolved extension environment placeholders become empty strings so literal `$VAR` tokens are not passed to MCP processes.

Use `config.example.yaml` and `extensions_config.example.json` as non-secret shape references. Do not document or commit values from `.env`, `config.yaml`, or a private extensions file.

## State Paths

`Paths.base_dir` resolves constructor override, `AGENTFLOW_HOME`, compatibility `DEER_FLOW_HOME`, then repository-local `.agentflow`. It owns global and per-agent memory, custom-agent directories, thread uploads/outputs, and host mount paths. `_validate_thread_id` rejects characters outside alphanumerics, underscore, and hyphen. `resolve_virtual_path` enforces the `/mnt/user-data` prefix and verifies the resolved target remains inside the thread data directory.

See [sessions and files](../state/sessions-and-files.md) for the state layout and [security boundaries](../operations/security-and-boundaries.md) for path implications.

## Change Recipe

When adding a setting, update the owning Pydantic config type, add the field to `AppConfig` if it is a top-level domain, load/reset any module-level config holder in `from_file`, and add a placeholder-safe example. If the setting affects agent composition, include [lead-agent](../runtime/lead-agent.md) tests or the owning middleware suite. If it changes a path, cover precedence, traversal, and repository-root resolution in `test_paths_config.py`.

The focused command is `PYTHONPATH=backend python -m pytest backend/tests/test_config_policy.py backend/tests/test_paths_config.py -q`. Configuration policy tests inspect the current private `config.yaml`, so failures can indicate local policy drift as well as code defects.
