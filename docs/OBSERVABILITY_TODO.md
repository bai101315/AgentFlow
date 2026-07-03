# AgentFlow Observability TODO

AgentFlow needs a local-first observability system for debugging agent behavior, tool use, token cost, cache behavior, cross-session performance, and future agent training data collection.

LangSmith solves part of this problem, but it is often too remote, too heavy, or too hard to search when debugging a local personal assistant runtime.

## Goals

- Show each answer's observable reasoning path: planner steps, selected tools, memory/session recall, cache decisions, and final response path.
- Do not store or display private hidden chain-of-thought. Store safe reasoning summaries, model-visible planning artifacts, tool decisions, and runtime events.
- Record token usage for every model call and every user turn.
- Record cache hits for prompt cache, provider cache metadata, skill/system-prompt cache, MCP tool cache, memory cache, and session-search cache where available.
- Aggregate cache/token/tool/runtime totals after a session ends.
- When a user reopens the same `thread_id`, continue accumulating metrics on top of previous totals.
- Record every tool call with name, call id, sanitized args, result preview, status, error, retry count, and elapsed time.
- Export successful and failed tool-use trajectories for future agent RL training and eval.

## Trajectory Dataset

AgentFlow should be able to export local traces into JSONL files:

- `trajectory_samples.jsonl`: successful completed conversations, `completed=true`.
- `failed_trajectories.jsonl`: failed, interrupted, timed-out, or cancelled conversations, `completed=false`.

Each JSONL record should include:

- `trajectory_id`
- `thread_id`
- `trace_id`
- `turn_ids`
- `user_goal`
- `messages`
- `tool_calls`
- `final_answer`
- `completed`
- `failure_reason`
- `token_usage`
- `cache_summary`
- `elapsed_ms`
- `created_at`

Tool call records should include:

- `tool_call_id`
- `tool_name`
- `args_preview`
- `args_hash`
- `result_preview`
- `result_hash`
- `status`
- `error_type`
- `elapsed_ms`
- `retry_count`

This dataset can later support:

- tool-use eval cases
- supervised fine-tuning style behavior review
- agent RL reward modeling
- failure clustering
- regression replay
- comparing prompt/tool-policy changes

All exported trajectories must run through redaction before writing to disk.

## Data Model

- `trace_id`: one user request from input to final answer.
- `thread_id`: persistent conversation identity; used for cross-session totals.
- `turn_id`: one user message and the agent's full response.
- `span_id`: a timed child operation such as model call, tool call, memory read, session search, MCP init, cache lookup, or subagent run.
- `session_totals`: persistent aggregate keyed by `thread_id`.

Minimum fields:

- time: `started_at`, `ended_at`, `elapsed_ms`
- model: provider, model name, thinking enabled, retry count
- tokens: input, output, total, cached input, cache write, cache read if available
- cache: cache name, hit/miss/stale, key hash, lookup time
- tools: name, tool_call_id, args_preview, result_preview, status, error_type, elapsed_ms
- safety: redaction applied, truncated fields, secret patterns detected
- trajectory: completed, failure_reason, export_target

## Implementation TODO

- [ ] Add `.agentflow/observability.db` with tables for traces, spans, tool_calls, model_calls, cache_events, thread_totals, and trajectory_exports.
- [ ] Add an `ObservabilityRecorder` service with `start_trace`, `end_trace`, `start_span`, `end_span`, `record_cache_event`, and `mark_completed`.
- [ ] Wrap `DeerFlowClient.stream()` so every user turn creates a trace and writes final turn totals.
- [ ] Normalize `usage_metadata` from LangChain messages into a stable AgentFlow token schema.
- [ ] Add an agent middleware for model-call timing and token usage.
- [ ] Add a tool middleware around `wrap_tool_call` and `awrap_tool_call` to measure tool latency and capture sanitized results.
- [ ] Add cache instrumentation for MCP tool cache, skills prompt cache, session search, memory injection, and config reload.
- [ ] Persist thread-level totals so reopened sessions continue from previous `thread_id` metrics.
- [ ] Add trajectory export:
  - successful completed traces go to `trajectory_samples.jsonl`
  - failed/interrupted traces go to `failed_trajectories.jsonl`
- [ ] Add redaction for API keys, GitHub tokens, LeetCode sessions, bearer tokens, cookies, file paths if needed, and long file contents.
- [ ] Emit optional stream events like `observability.trace_started`, `observability.tool_finished`, and `observability.turn_summary`.
- [ ] Add a CLI command or script to inspect recent traces, slowest tools, failed tools, token-heavy turns, cache hit rate, and trajectory export stats.
- [ ] Add tests for aggregation, reopened-session accumulation, tool timing, token normalization, redaction, and successful/failed trajectory export.

## Test Plan

- Unit test token aggregation from multiple `AIMessage.usage_metadata` chunks without double counting.
- Unit test reopened `thread_id` accumulation across two client instances.
- Unit test tool span timing for success, retry, and failure paths.
- Unit test secret redaction for tokens, cookies, API keys, long outputs, and tool args.
- Unit test successful trace export to `trajectory_samples.jsonl`.
- Unit test failed/interrupted trace export to `failed_trajectories.jsonl`.
- Integration smoke test: run one agent turn, call one tool, verify trace rows, thread totals, and trajectory export records are created.

## Assumptions

- This document is the implementation backlog for `docs/OBSERVABILITY_TODO.md`.
- The first implementation should use local SQLite and JSONL export only; do not add LangSmith as a required dependency.
- "Reasoning process" means safe observable runtime trajectory, not private hidden chain-of-thought.
- All cross-session accumulation is keyed by `thread_id`.
- Trajectory data stores redacted and truncated content by default to avoid leaking local privacy or secrets into training data.
