# Project Direction

AgentFlow is being narrowed around two priorities:

- Local coding-agent reliability
- Tool-use evaluation

The goal is not to build a broad personal-assistant product right now. The goal
is to make one local agent runtime that can reliably read code, edit code, run
focused checks, recall previous project context, and be evaluated.

## In Scope

- Lead coding agent runtime
- Local tools and explicit command/file permission boundaries
- Skill management as an explicit foreground capability
- Session search for cross-session recall
- Focused tests and evals for tool-use behavior
- Safe showcase configuration and documentation

## Out of Scope For Now

- Broad gateway/dashboard product work
- Background automatic skill mutation by default
- Curator LLM consolidation by default
- Large multi-agent platform work
- Broad MCP marketplace behavior
- Any feature that cannot be tested or explained simply

## Default Risk Policy

- `sandbox.allow_host_bash: false` in example config
- `background_review.enabled: false`
- `curator.enabled: false`
- `skill_evolution.auto_create: false`
- `session_search.enabled: true`

The next engineering priority is a small, stable evaluation harness before
adding new user-facing features.
