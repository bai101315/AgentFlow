---
type: Model Integration Guide
title: Model Factory and Selection
description: Configured chat-model resolution, provider reflection, thinking and reasoning controls, per-agent overrides, and tracing callback attachment.
tags: [models, providers, configuration]
openwiki:
  roles: [integration, architecture]
  source_paths: [backend/models/factory.py, backend/config/model_config.py, backend/agents/lead_agent/agent.py]
  symbols: [create_chat_model, _resolve_model_name, ModelConfig]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_smoke_imports.py -q]
---

# Model Factory and Selection

`create_chat_model` is the provider-neutral factory. It looks up a `ModelConfig`, resolves its `use` class through reflection as a `BaseChatModel`, strips AgentFlow-only metadata, merges thinking settings and caller overrides, instantiates the provider, and attaches configured tracing callbacks.

Thinking behavior is capability-aware. `when_thinking_enabled`, the shortcut `thinking`, and `when_thinking_disabled` are merged into provider constructor settings. Reasoning-effort models remove incompatible `max_tokens` and map disabled thinking to `reasoning_effort="none"`; enabled calls accept low through xhigh or default to medium.

The [lead agent](../runtime/lead-agent.md) applies runtime and custom-agent precedence before calling the factory. `_resolve_model_name` prefers the configured `deepseek-v4` profile, then the first model. The embedded client calls the same factory but has its own lazy agent cache, so model-setting changes may require `reset_agent`.

When adding a provider/profile capability, update `ModelConfig`, factory normalization, examples with environment placeholders, and lead-agent capability handling. Test class resolution, override precedence, enabled/disabled thinking, unsupported capabilities, and consumer construction. Do not run live model calls for ordinary validation; use fakes and the smoke import suite. A provider integration check is conditional on changing provider-specific request shape.
