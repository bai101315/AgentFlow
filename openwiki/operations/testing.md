---
type: Testing Guide
title: Testing and Narrow Validation
description: Core test conventions, behavior-to-suite routing, quiet commands, and conditions for broader or live validation.
tags: [testing, validation, pytest]
openwiki:
  roles: [testing, repository]
  source_paths: [backend/tests, backend/TESTING.md, pytest.ini]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests -q]
---

# Testing and Narrow Validation

The core suite is under `backend/tests` and is designed to avoid live model and network calls. Tests commonly use `tmp_path`, inject configuration with `set_app_config`, and mock model/security boundaries. Set `PYTHONPATH=backend` so top-level imports such as `agents`, `config`, and `skill` resolve.

Use the narrowest quiet command that proves the behavior:

| Behavior | Focused suite |
|---|---|
| Configuration policy and path roots | `test_config_policy.py test_paths_config.py` |
| Memory filtering and hook guards | `test_memory_middleware.py` |
| Search schema, dedupe, retrieval, retention | `test_session_search_core.py` |
| Skill mutation provenance and scanning | `test_skill_manage_core.py test_skill_security_scanner.py` |
| Curator transitions and middleware | `test_curator_lifecycle.py test_curator_middleware.py` |
| Review trigger and isolated runtime | `test_background_review_trigger.py test_review_runtime.py` |
| Skill discovery/access/usage | `test_skill_view_tools.py test_skill_usage_wiring.py` |
| Observability store and recorder | `test_observability_core.py` |
| Subagent aliases/config overrides | `test_subagents_registry.py` |
| Package health | `test_smoke_imports.py` |

Example: `PYTHONPATH=backend python -m pytest backend/tests/test_session_search_core.py -q`. `-q` suppresses successful detail while retaining failure diagnostics.

Run `PYTHONPATH=backend python -m pytest backend/tests -q` when changing shared agent composition, configuration models used across systems, common event/provenance records, or several subsystem contracts. Live provider, MCP transport, PostgreSQL, browser UI, and performance checks are conditional on changes to those integrations; they should use controlled credentials/environments and are not ordinary validation.

A test should assert externally visible state or output. For lifecycle systems include disabled/initial state, transitions in both directions where supported, unchanged repeated updates, missing prerequisites, instance/thread isolation, reset or shutdown boundaries, deferred mutation, and composition with policy constraints. Stable test names listed in each concept page are preferred retrieval anchors.
