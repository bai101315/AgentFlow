---
type: Security Guide
title: Security and Trust Boundaries
description: Secret handling, host command policy, path traversal protections, tool and skill governance, review isolation, and observability redaction.
tags: [security, boundaries, secrets]
openwiki:
  roles: [operations, architecture]
  source_paths: [backend/sandbox/security.py, backend/config/paths.py, backend/tools/tools.py, backend/tools/skill_manage_tool.py, backend/observability/redaction.py]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_paths_config.py backend/tests/test_skill_manage_core.py backend/tests/test_observability_core.py -q]
---

# Security and Trust Boundaries

AgentFlow is a local runtime, not a hardened remote multi-tenant sandbox. The example configuration uses `LocalSandboxProvider` with host bash disabled, but `Paths.sandbox_work_dir` currently exposes the repository root for local project work. Treat enabled file-write or host-command tools as trusted-user capabilities.

## Boundaries

- Secrets belong in environment variables or private local configuration. `.env`, `config.yaml`, private extension files, `.agentflow`, databases, and logs must not be committed. Example files contain placeholders only.
- The [tool registry](../tools/registry-and-governance.md) removes configured bash tools and hides the bash subagent unless `is_host_bash_allowed` passes. New command surfaces must participate in the same classification.
- `Paths` validates thread IDs and resolves virtual files beneath `/mnt/user-data`, rejecting prefix confusion and canonical path traversal.
- [Governed skill writes](../skills/governed-writes.md) validate names/frontmatter/support paths, scan content, serialize writes, and constrain automated provenance. Executable support files require an allow decision.
- [Background review](../skills/background-review.md) has a distinct thread, immutable transcript, no checkpointer, no network/bash/MCP tools, and a restricted write schema.
- [Observability](../state/observability.md) redacts and hashes previews, caps length, and supports content mode `off`. New captured fields must pass the same policy.
- MCP OAuth and server environment values are sensitive configuration. Do not log resolved values or include them in documentation examples.

A security-affecting change needs negative tests: traversal attempts, invalid identifiers, disabled-host-bash exposure, public/pinned/user-owned skill writes, scanner blocks, provenance spoofing, redaction of representative token forms, and review tool isolation. Run the three focused suites in metadata; include subagent/review tests when those boundaries change.
