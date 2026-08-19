---
type: Background Workflow
title: Background Skill Review
description: Tool-call-triggered isolated review that converts durable conversation lessons into provenance-governed custom skill changes without affecting foreground turns.
tags: [skills, review, concurrency]
openwiki:
  roles: [workflow, domain]
  source_paths: [backend/agents/middlewares/background_review_middleware.py, backend/agents/review_agent/runtime.py]
  symbols: [BackgroundReviewMiddleware, ReviewRequest, run_review, ReviewScheduler]
  test_paths: [backend/tests/test_background_review_trigger.py, backend/tests/test_review_runtime.py]
  invariants: [The reviewer receives an immutable transcript and a distinct thread id., Review has no checkpointer and only skill tools., Review failure or timeout never raises into the foreground turn.]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_background_review_trigger.py backend/tests/test_review_runtime.py -q]
---

# Background Skill Review

Background review learns reusable workflows after enough foreground tool calls. `BackgroundReviewMiddleware` is only the trigger: its `after_agent` hook snapshots messages, updates per-thread counters, schedules work, and returns `None` immediately.

```mermaid
sequenceDiagram
    participant Foreground
    participant Trigger as Review middleware
    participant Scheduler
    participant Reviewer
    participant Skills as Governed skill store
    Foreground->>Trigger: Completed state snapshot
    Trigger->>Trigger: Count unseen tool calls
    Trigger->>Scheduler: Submit immutable ReviewRequest
    Scheduler-->>Foreground: Return without waiting
    Scheduler->>Reviewer: Run isolated agent
    Reviewer->>Skills: List view and governed writes
    Skills-->>Reviewer: Validated results
    Reviewer-->>Scheduler: ReviewResult only
```

The foreground sees neither reviewer messages nor its internal state.

A `skill_manage` call in the foreground resets the nudge count. At threshold, one review per thread may run; global concurrency is bounded, and excess triggers are dropped rather than queued. Per-thread tool-call IDs are deduplicated and stale in-memory thread state is pruned.

`ReviewRequest` is frozen, carries a distinct `review_thread_id`, and contains plain transcript text rather than mutable message objects. The review agent has no checkpointer and only `skills_list`, `skill_view`, and a provenance-bound restricted `skill_manage`. Its sole middleware converts recoverable tool errors into `ToolMessage`s. `run_review` returns structured completed, timeout, or failed results and never intentionally raises. Writes completed before timeout remain valid and journalled.

The scheduler runs reviews in daemon threads with a bounded semaphore. CLI shutdown waits a bounded interval through `wait_for_idle`, linking this lifecycle to [CLI workflows](../operations/cli-and-workflows.md). Review events are visible through [observability](../state/observability.md).

Test threshold transitions, repeated snapshots, foreground skill reset, independent threads, one-review-per-thread, global saturation/drop, immutable request, tool isolation, provenance, timeout with partial writes, failure return, final-summary-only boundary, and shutdown wait. Use the two focused suites; skill policy changes also require [governed-write](governed-writes.md) tests.
