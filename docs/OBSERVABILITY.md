# AgentFlow Observability

This document describes the observability system that is already implemented in AgentFlow today: how it works, how to use it, what it saves, and how to inspect failures that happened during a turn even if the agent later recovered.

## What It Is

AgentFlow now has a local-first observability layer designed for day-to-day debugging and demo use.

Its goals are:

- record each user turn as a trace
- accumulate totals by `thread_id` across reopened sessions
- capture model usage, prompt-cache behavior, tool calls, latency, and failure signals
- keep the storage local and stable
- avoid storing private hidden chain-of-thought
- make hidden failures visible to the developer even when the final answer succeeded

This system is implemented as a low-coupling standalone module under `backend/observability`.

## Current Architecture

Main modules:

- `backend/observability/store.py`
  - SQLite-backed canonical store
- `backend/observability/recorder.py`
  - trace lifecycle, aggregation, summary generation, trajectory export
- `backend/observability/middleware.py`
  - LangChain middleware for model-call and tool-call instrumentation
- `backend/observability/redaction.py`
  - secret masking, truncation, stable hashing
- `backend/observability/summary_view.py`
  - thread summary and compatibility `session_logs` view generation
- `backend/observability/trajectory.py`
  - JSONL export helper
- `backend/config/observability_config.py`
  - observability config schema

Integration points:

- `main.py`
  - starts and ends one trace per user input
  - supports `/obs` runtime commands
  - prints developer-facing failure warnings after a turn if any tool failed mid-turn
- `make_lead_agent(..., custom_middlewares=[ObservabilityMiddleware()])`
  - injects model/tool instrumentation without tightly coupling business logic to storage

## Storage Model

The system uses one canonical source of truth plus human-readable views.

Canonical store:

- `.agentflow/observability.db`

Human-readable files:

- `.agentflow/observability/threads/{thread_id}.json`
- `.agentflow/session_logs/{thread_id}.json`

Detailed trace files:

- `.agentflow/observability/traces/{yyyy-mm-dd}/{thread_id}/{hh-mm-ss}-{traceid8}.json`

Trajectory export:

- `.agentflow/observability/trajectory_samples.jsonl`
- `.agentflow/observability/failed_trajectories.jsonl`

## Important Design Choice

AgentFlow does not write one large detailed JSON file for every single turn by default.

Instead:

- every turn is always written to SQLite
- every thread always has a stable summary JSON
- compatibility `session_logs/{thread_id}.json` is still written
- detailed per-trace JSON files are written only when they are useful

This keeps normal usage lighter and easier to inspect.

Detailed trace files are currently written when:

- the trace failed
- observability mode is `full`
- the trace is anomalous:
  - slow trace
  - high billable input tokens
  - low prompt cache hit rate

## Runtime Concepts

- `thread_id`
  - persistent conversation identity
  - reopened sessions continue accumulating totals on top of the same thread
- `trace_id`
  - one user input to one final answer
- `model_call`
  - one observed model invocation with token usage and timing
- `tool_call`
  - one observed tool invocation with args preview, result preview, status, and timing

## What Gets Recorded

### Per trace

Each trace stores:

- `trace_id`
- `thread_id`
- `agent_name`
- `model_name`
- `started_at`
- `ended_at`
- `elapsed_ms`
- `completed`
- `failure_reason`
- `content_mode`
- `user_input_preview`
- `assistant_output_preview`
- aggregated token/cache usage
- tool call count
- failure summary
- diagnostics summary
- model call records
- tool call records
- cache events

### Failure summary

Each trace also stores failure-awareness fields:

- `had_any_failure`
- `failed_tool_call_count`
- `failed_tool_names`
- `recovered_after_failure`
- `failure_events`

This is the key behavior for developer visibility:

- if a tool failed once and later succeeded, the turn can still end with `completed=true`
- but `had_any_failure=true` and `recovered_after_failure=true`
- CLI will print a warning after the turn
- thread summary will also preserve that signal

### Per tool call

Each tool call stores:

- `tool_call_id`
- `tool_name`
- `status`
- `error_type`
- `elapsed_ms`
- `retry_count`
- `args_preview`
- `args_hash`
- `result_preview`
- `result_hash`

### Per model call

Each model call stores:

- `model_name`
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `billable_input_tokens`
- `prompt_cache_hit_tokens`
- `prompt_cache_miss_tokens`
- `prompt_cache_hit_rate`
- `elapsed_ms`
- preview text summary

### Per thread totals

Thread-level summary accumulates across the same `thread_id`:

- `trace_count`
- `turn_count`
- `total_input_tokens`
- `total_output_tokens`
- `total_tokens`
- `total_billable_input_tokens`
- `total_prompt_cache_hit_tokens`
- `total_prompt_cache_miss_tokens`
- `prompt_cache_hit_rate`
- `tool_call_count`
- `failed_tool_call_count`
- `trace_with_failures_count`
- `recovered_trace_count`
- `top_failed_tools`
- `last_failed_tools`
- `slow_trace_count`
- `expensive_trace_count`
- `low_cache_trace_count`
- last-trace summary fields

## Redaction and Privacy

The system does not store model private hidden chain-of-thought.

It stores safe observable runtime artifacts only:

- input/output previews
- tool selection and tool results
- token usage
- cache behavior
- timing
- failure summaries

Before content is saved, previews go through redaction and truncation.

Current masking rules cover common secrets such as:

- OpenAI-style keys like `sk-...`
- GitHub tokens like `ghp_...`
- `Bearer ...`
- `LEETCODE_SESSION=...`
- common `api_key`, `token`, `secret`, `cookie`, `session` patterns

The recorder also stores a stable hash for args/results/content so the developer can correlate repeated values without always saving the full original content.

## Content Modes

Current runtime modes:

- `/obs summary`
  - default mode
  - save redacted summary previews
- `/obs full`
  - save more complete redacted content
  - also forces detailed trace file writing
- `/obs off`
  - keep metrics and structure, but do not save content previews
- `/obs status`
  - print current observability state and current thread totals

These commands are handled directly in `main.py`.

## How a Turn Is Recorded

The current flow is:

1. user enters input in CLI
2. `main.py` calls `observability_recorder.start_trace(...)`
3. `trace_id` is attached to runtime metadata
4. `ObservabilityMiddleware` captures:
   - model-call timing and usage
   - tool-call timing, status, errors, args preview, result preview
5. after the final answer, `main.py` calls `observability_recorder.end_trace(...)`
6. recorder:
   - builds trace summary
   - computes failure summary
   - writes canonical trace row to SQLite
   - updates thread totals
   - writes thread summary JSON
   - writes compatibility `session_logs` JSON
   - exports trajectory JSONL
   - optionally writes a detailed trace JSON file
7. if the turn had any failed tool calls, `main.py` prints a short warning

## Failure Warning Behavior

If any tool failed during the turn, even when the final answer succeeded, the CLI prints a short warning.

Typical output shape:

```text
Observability warning:
  this turn had 1 failed tool call(s)
  failed tools: read_file
  recovered: yes
  thread summary: .agentflow/observability/threads/{thread_id}.json
  trace file: .agentflow/observability/traces/...
```

This does not interrupt the agent and does not change recovery behavior. It is a developer-facing signal only.

## Diagnostics and Trace File Triggers

Recorder currently marks a trace as diagnostically interesting when one of these is true:

- `elapsed_ms >= 10000`
- `billable_input_tokens >= 4000`
- `prompt_cache_hit_rate < 0.5`

When enabled, those cases can trigger detailed trace JSON output even if the run did not fail.

## Trajectory Export

The system also exports traces into JSONL for future replay, eval, or RL-style data preparation.

Successful traces:

- `.agentflow/observability/trajectory_samples.jsonl`

Failed traces:

- `.agentflow/observability/failed_trajectories.jsonl`

Each row currently includes:

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

## How to Use It

### 1. Run the normal CLI

Start AgentFlow as usual. Observability is enabled by default.

### 2. Inspect current status

In the CLI:

```text
/obs status
```

This shows:

- whether observability is enabled
- current content mode
- database location
- thread summary location
- total tokens so far
- current prompt cache hit rate

### 3. Switch capture mode when needed

Use:

```text
/obs summary
/obs full
/obs off
```

Recommended usage:

- normal development: `summary`
- difficult bug hunt: `full`
- privacy-sensitive debugging: `off`

### 4. Read the thread summary first

Best first inspection target:

- `.agentflow/observability/threads/{thread_id}.json`

This gives a stable, session-oriented summary of:

- recent traces
- token totals
- cache rate
- tool calls
- failure history
- latest trace diagnostics

### 5. Open detailed trace files only when needed

If CLI points to a trace file, or if a trace was marked anomalous, inspect:

- `.agentflow/observability/traces/...`

This is the best place for one-turn deep debugging.

### 6. Use `session_logs` only as a compatibility view

The file:

- `.agentflow/session_logs/{thread_id}.json`

still exists and is human-readable, but it is no longer the canonical source of truth. The canonical source is the observability store.

## What Is Saved by Default

Default mode is `summary`.

That means AgentFlow saves:

- SQLite trace rows
- thread summary JSON
- compatibility session summary JSON
- trajectory JSONL
- redacted/truncated input and output previews
- redacted/truncated tool args and tool results
- token usage
- cache metrics
- tool failure summaries

It does not save by default:

- private hidden chain-of-thought
- a detailed JSON file for every single turn

## Current Config Knobs

Current config fields include:

- `enabled`
- `content_mode`
- `capture_tool_args`
- `capture_tool_results`
- `max_preview_chars`
- `export_trajectories`
- `write_session_summary_view`
- `write_trace_files_on_failure`
- `write_trace_files_on_full`
- `write_trace_files_on_anomaly`
- `slow_trace_ms`
- `high_billable_input_tokens`
- `low_cache_hit_rate`

These are defined in `backend/config/observability_config.py`.

## Current Limitations

The current implementation is already useful, but it is still a local debugging layer, not a full tracing product.

Current limitations:

- no web dashboard
- no dedicated trace query CLI yet
- cache instrumentation is strongest for prompt/model usage; broader cache domains can still be expanded
- trajectory export is useful now, but future redaction rules may need to become more fine-grained
- summary and trace files are for inspection, not yet for rich UI playback

## Recommended Inspection Order

When debugging a strange turn:

1. look at CLI warning output
2. open `.agentflow/observability/threads/{thread_id}.json`
3. inspect `latest_trace`
4. check `had_any_failure`, `failed_tool_names`, `recovered_after_failure`
5. check token totals and prompt cache hit rate
6. if needed, open the detailed trace JSON file
7. if comparing behavior over time, inspect `trajectory_samples.jsonl` or `failed_trajectories.jsonl`

## Related Documents

- `docs/OBSERVABILITY_TODO.md`
  - future expansion backlog
- `docs/DEMO.md`
  - interview/demo flow
