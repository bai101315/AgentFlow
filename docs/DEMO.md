# AgentFlow Interview Demo

This demo is designed to show that AgentFlow is more than a chat wrapper. It demonstrates tool use, memory, session recall, skill evolution, and verification.

## Demo Story

1. Start with a small repository that has one failing test.
2. Ask AgentFlow to inspect the failure and fix it.
3. The agent reads files, edits the bug, and runs the focused test.
4. After the fix, the agent captures the reusable debugging pattern as a custom skill.
5. Start a later session and ask what was decided last time; the agent uses session search to recall the earlier context.

## What To Show

- The agent uses file tools instead of guessing.
- The agent runs a real test command and reports the result.
- The agent avoids broad unrelated edits.
- `skills/custom/` records a reusable workflow.
- `.agentflow/session_search.db` can recall prior discussion.
- Runtime state is local and ignored by git.

## Suggested Script

```text
User: Find why the focused test is failing and fix it.
Agent: Reads the test, reads the implementation, edits one file, runs the test.

User: Save the reusable debugging pattern if it is worth reusing.
Agent: Uses skill_manage to create or patch a custom skill.

New session:
User: What did we decide last time about the debugging workflow?
Agent: Uses session_search and summarizes the previous decision.
```

## Talking Points

- Memory is for durable facts; session search is for historical recall.
- Skills store reusable procedures and are managed through a validated write path.
- MCP tools are deferred behind tool search to control prompt size.
- Host bash is explicit opt-in because local command execution is a major trust boundary.
