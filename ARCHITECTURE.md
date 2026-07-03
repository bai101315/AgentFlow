# AgentFlow Architecture

AgentFlow is a local agent runtime for learning how reliable personal assistants are built. The architecture is intentionally centered on runtime concerns rather than a broad product surface.

## 1. Agent Runtime

The lead agent is built on LangChain/LangGraph. Runtime configuration selects the model, thinking mode, custom agent name, plan mode, and subagent support. Custom agents can have their own `SOUL.md`, memory file, model overrides, tool groups, and skills.

The CLI in `main.py` creates or resumes the configured agent and stores runtime state under `.agentflow/` by default.

## 2. Tool Governance

Tools are configured by group in `config.yaml`, then loaded at runtime. Local file tools, bash, web search, built-in tools, MCP tools, and subagent tools are composed through `backend/tools/tools.py`.

MCP tools can be deferred behind `tool_search`, so the model sees a small discovery surface instead of every external schema. Host bash is disabled by default in example config because the local provider is not a strong sandbox boundary.

## 3. Memory

Memory stores durable facts, not transient task logs. The memory middleware filters completed conversation turns, detects correction or reinforcement signals, and queues updates for LLM summarization. Per-agent memory lets different custom agents keep separate long-term context.

## 4. Session Search

Session search stores searchable copies of user messages and final assistant replies in SQLite FTS5. It supports query, recent-session browse, full session read, and around-message inspection. This is separate from memory: session search recalls what happened, while memory stores what should remain true later.

## 5. Skill Evolution

Skills are reusable workflow documents under `skills/`. Public skills are read-only; custom skills can be created or patched through `skill_manage`. That tool validates skill names and markdown, runs safety scanning, writes history, updates usage metadata, and refreshes the prompt cache.

## Runtime State

Default runtime state lives under:

```text
.agentflow/
|-- agents/
|-- threads/
|-- session_logs/
|-- checkpoints.db
`-- session_search.db
```

`AGENTFLOW_HOME` overrides this location. `DEER_FLOW_HOME` remains a compatibility fallback for older local setups.
