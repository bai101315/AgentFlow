---
type: Documentation Reference
title: AgentFlow Wiki Coverage
description: Coverage ledger for the generated AgentFlow code wiki and its canonical concept boundaries.
tags: [documentation, coverage]
openwiki:
  roles: [repository]
  source_paths: [openwiki/quickstart.md]
---

# AgentFlow Wiki Coverage

The wiki is organized by runtime responsibility and change intent. Start at [quickstart](quickstart.md), which links every major concept and routes implementation work to exact source symbols, focused tests, and narrow commands.

Canonical areas are [architecture](architecture/overview.md), [runtime composition](runtime/lead-agent.md), [tools and integrations](tools/registry-and-governance.md), [state and persistence](state/sessions-and-files.md), [skill evolution](skills/library.md), and [operations/testing](operations/testing.md). The [source map](reference/source-map.md) is a compact locator rather than a duplicate architecture description.

Known evidence gaps are tracked in the quickstart backlog. Git range inspection was unavailable under active `.openwikiignore`, and dedicated consumer tests are absent for the embedded client, checkpoint factories, MCP cache/deferred registry, and CLI orchestration.
