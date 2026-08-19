---
type: Runtime Reference
title: Middleware Lifecycle
description: AgentFlow middleware families, hook responsibilities, ordering constraints, and routes to stateful subsystem tests.
tags: [middleware, lifecycle, errors]
openwiki:
  roles: [architecture, workflow]
  source_paths: [backend/agents/middlewares, backend/agents/lead_agent/agent.py]
  symbols: [_build_middlewares, build_lead_runtime_middlewares, build_subagent_runtime_middlewares]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_memory_middleware.py backend/tests/test_background_review_trigger.py -q]
---

# Middleware Lifecycle

Middleware is the integration layer between the compiled agent and AgentFlow's stateful services. The canonical ordering lives in `_build_middlewares` in `backend/agents/lead_agent/agent.py`; shared lead/subagent error and runtime middleware are built in `tool_error_handling_middleware.py`.

| Family | Primary module | Runtime effect |
|---|---|---|
| Prompt and context | `prompt_cache_middleware.py`, `thread_data_middleware.py`, `uploads_middleware.py` | Builds cached system context and makes thread paths/uploads available. |
| Model shaping | `todo_middleware.py`, `title_middleware.py`, `clarification_middleware.py`, `loop_detection_middleware.py` | Adds planning/title behavior, breaks repetitive calls, and converts clarification into the final interaction boundary. |
| Error repair | `dangling_tool_call_middleware.py`, `tool_error_handling_middleware.py`, `llm_error_handling_middleware.py` | Repairs history and turns supported failures into model-visible messages. |
| Durable side effects | `memory_middleware.py`, `session_search_middleware.py` | Queues durable-memory updates and indexes searchable final turns after agent completion. |
| Self-improvement | `background_review_middleware.py`, `curator_middleware.py` | Schedules isolated review and periodic skill lifecycle maintenance. |
| Deferred tools | `deferred_tool_filter_middleware.py` | Hides deferred schemas until `tool_search` promotes them. |

[Memory](../state/memory.md), [session search](../state/session-search.md), [background review](../skills/background-review.md), and [curator lifecycle](../skills/curator-lifecycle.md) are canonical for their state transitions. [Observability](../state/observability.md) is injected as custom middleware by the CLI rather than hard-coded into the builder.

## Extension Rules

For a new middleware, define the narrow state schema, choose before/after/wrap hooks deliberately, and ensure a disabled feature is inert. Do not assume `runtime.context` always contains a thread ID; existing stateful middleware falls back to LangGraph configurable metadata. Side-effect middleware must not change the foreground result and should isolate failures.

Test initial disabled state, enabled transition, unchanged/repeated snapshots, missing thread or messages, independent thread identity, and shutdown/reset behavior where applicable. Run the owning test file; changes to common error middleware or order require `PYTHONPATH=backend python -m pytest backend/tests -q`.
