# Files

- [Checkpointing and Thread Resume](checkpointing.md) - Sync and async LangGraph checkpointer factories, backend selection, lifecycle cleanup, and the relationship between thread IDs and resumable state.
- [Embedded Python Client](embedded-client.md) - Public `DeerFlowClient` lifecycle, synchronous streaming contract, lazy agent cache, thread APIs, and consumer-facing validation requirements.
- [Lead Agent Composition](lead-agent.md) - Model resolution, prompt caching, tool selection, middleware ordering, and state schema used to compile the primary AgentFlow agent.
- [Middleware Lifecycle](middleware.md) - AgentFlow middleware families, hook responsibilities, ordering constraints, and routes to stateful subsystem tests.
- [Subagent Delegation](subagents.md) - Subagent registry, task tool, executor state machine, tool filtering, event-loop isolation, polling, cancellation, and focused validation.
