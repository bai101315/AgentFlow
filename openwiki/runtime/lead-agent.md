---
type: Runtime Guide
title: Lead Agent Composition
description: Model resolution, prompt caching, tool selection, middleware ordering, and state schema used to compile the primary AgentFlow agent.
tags: [runtime, agent, middleware]
openwiki:
  roles: [architecture, workflow]
  source_paths: [backend/agents/lead_agent/agent.py, backend/agents/lead_agent/prompt.py, backend/agents/thread_state.py]
  symbols: [make_lead_agent, _build_middlewares, _resolve_model_name, ThreadState]
  test_paths: [backend/tests/test_smoke_imports.py]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q]
---

# Lead Agent Composition

`make_lead_agent` is the canonical runtime compiler used by the CLI. Consult it for model precedence, tool groups, plan mode, bootstrap behavior, prompt caching, or middleware composition.

Model selection is request override, then custom-agent profile, then `_resolve_model_name`; the latter prefers `deepseek-v4` when configured and otherwise the first model. Per-agent `provider_model`, `api_key`, and `base_url` override constructor settings while retaining a valid global model profile. Unsupported thinking mode is disabled with a warning.

Before compilation, the builder warms the enabled-skill cache and creates a `PromptCacheMiddleware` signature from model, agent, skill, bootstrap, and subagent inputs. Both normal and bootstrap agents use `PROMPT_CACHE_PLACEHOLDER`; the middleware supplies the actual session prompt. Bootstrap adds `setup_agent`; normal mode filters configured tools by the custom agent's tool groups.

The [tool registry](../tools/registry-and-governance.md) supplies tools and [middleware](middleware.md) supplies lifecycle hooks. `ThreadState` is the shared graph schema; its artifact reducer deduplicates while preserving order. A caller-provided [checkpointer](checkpointing.md) is passed directly to `create_agent`.

## Middleware Order

`_build_middlewares` starts with `build_lead_runtime_middlewares`, then conditionally appends prompt cache, todo, title, memory, session search, background review, curator, deferred-tool filtering, loop detection, custom middleware, and finally clarification. Summarization and token-usage middleware code is currently disabled in this builder despite configuration types existing.

Ordering is externally significant: prompt construction must happen before model calls; state-producing/error-handling middleware must precede consumers; clarification is last. When adding middleware, identify its hook, state fields, exception semantics, and exact position. Test enabled, disabled, missing-prerequisite, repeated-turn, and independent-thread behavior in the owning suite.

Validate imports first with `PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q`, then run only the affected middleware suite. Use the full core suite when changing shared order or `ThreadState`.
