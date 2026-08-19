---
type: Public API Guide
title: Embedded Python Client
description: Public `DeerFlowClient` lifecycle, synchronous streaming contract, lazy agent cache, thread APIs, and consumer-facing validation requirements.
tags: [client, public-api, streaming]
openwiki:
  roles: [architecture, integration]
  source_paths: [backend/client.py, backend/__init__.py]
  symbols: [DeerFlowClient, StreamEvent, StreamEventType]
  invariants: [Multi-turn chat requires a checkpointer., AI text stream events are deltas and chat accumulates them.]
  validation_commands: [PYTHONPATH=backend python -c "import client; import backend.client; print('ok')"]
---

# Embedded Python Client

`DeerFlowClient` provides direct synchronous access without a gateway process. It loads configuration in `__init__` but defers model, tools, prompt, middleware, and graph creation until first use. `_ensure_agent` reuses the compiled agent while model, thinking, plan, subagent, agent-name, and available-skill inputs are unchanged; `reset_agent` forces recreation after external memory, skill, or configuration changes.

`stream` is a sync generator over LangGraph stream modes. `messages-tuple` AI text values are deltas with stable message IDs; consumers must accumulate content per ID. Tool calls/results are logical events and `values` events are full state snapshots. `chat` performs accumulation for callers that only need final text. This is intentionally separate from an asynchronous gateway pipeline.

The client accepts a checkpointer for multi-turn state. Without one it tries the package checkpointer factory; if no persistent backend is configured, persistence is in-process. Thread APIs such as `list_threads` and `get_thread` enumerate checkpoint data. File isolation can still use a thread ID even when conversational state is not persistent.

The client also exposes configuration/list/install helpers in the same module. Changes to skills or config that affect the prompt should call `reset_agent`; mutating files alone does not invalidate the agent's cache key.

## Public-Surface Checklist

A client change is complete only when the defining method, `StreamEvent` serialization, supported import paths (`client` and `backend.client` in the current smoke check), and a consumer invocation agree. Preserve sync-generator behavior, delta semantics, message serialization, and error visibility. The inspected suite lacks a dedicated client test module, so stream or thread changes should add consumer-level tests rather than relying only on `PYTHONPATH=backend python -c "import client; import backend.client; print('ok')"`.

The client depends on [configuration](../architecture/configuration.md), [tool governance](../tools/registry-and-governance.md), [middleware](middleware.md), and [checkpointing](checkpointing.md).
