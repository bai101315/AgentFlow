# AgentFlow Roadmap

## Phase 2: Evaluation Harness

- Build 10-20 deterministic tool-use cases.
- Track pass/fail, tool calls, runtime, token usage, and failure reason.
- Cover code reading, file edits, command execution, session recall, skill update, and unsafe-operation handling.

## Phase 3: Tool Permission Policy

- Add explicit permission levels: read, write, shell, network, credential.
- Require confirmation for destructive or credential-sensitive operations.
- Record audit logs for every tool call.

## Phase 4: Memory And Skill Approval

- Add optional approval queues for new memories and skill writes.
- Separate foreground user-requested writes from background self-improvement writes.
- Add rollback and diff views for custom skill changes.

## Phase 5: Subagent Reliability

- Add tests for subagent timeout, cancellation, and concurrency limits.
- Measure when subagents improve task quality versus adding overhead.
- Keep subagents disabled by default for simple tasks.
