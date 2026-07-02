# Project Direction

This project is being narrowed to an A + C model:

- A: local coding agent reliability
- C: tool-use evaluation

The goal is not to build a broad agent platform right now. The goal is to make
one local coding agent that can reliably read code, edit code, run focused
checks, recall previous project context, and be evaluated.

## In Scope

- Lead coding agent
- Local tools and sandboxed command/file operations
- Skill management as an explicit foreground capability
- Session search for cross-session recall
- Focused tests and evals for tool-use behavior

## Out of Scope For Now

- Background automatic skill mutation by default
- Curator LLM consolidation by default
- Multi-agent platform work
- Broad MCP marketplace/platform behavior
- Any feature that cannot be tested or explained simply

## Default Risk Policy

- `background_review.enabled: false`
- `curator.enabled: false`
- `skill_evolution.auto_create: false`
- `session_search.enabled: true`

The next engineering priority is a small, stable test harness before adding new
features.
