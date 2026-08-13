# AgentFlow

AgentFlow is a local agent runtime for coding, tool governance, memory, session recall, and skill evolution.

It is not trying to be a full OpenClaw or Hermes clone. The project focuses on the runtime pieces behind a personal assistant: how an agent gets tools safely, remembers useful context, resumes sessions, searches old conversations, and turns repeated workflows into reusable skills.

The current product surface is intentionally small: a local CLI and an embedded Python client. The engineering focus is reliability and explainability, especially for local coding-agent workflows.

## Design Goals

- Keep custom agents isolated by role definition, memory, thread, model profile, and tool groups.
- Avoid exposing every MCP or local tool to every model by default.
- Make long-running conversations resumable through checkpointed state.
- Preserve durable user and project context without storing transient task noise.
- Support skill creation and patching through a governed write path.
- Keep local execution practical on Windows while making permission boundaries explicit.
- Build toward tool-use evaluation instead of relying on ad hoc demos.

## Core Capabilities

- Agent runtime: create and run custom agents with per-agent SOUL, memory, model overrides, and tool permissions.
- Tool governance: configure tool groups, local file/bash tools, MCP tools, and deferred tool discovery through `tool_search`.
- Memory: store compact long-term facts and inject them into future sessions.
- Session search: index user and assistant turns into SQLite FTS for cross-session recall.
- Skills: load public/custom skills and update custom skills through `skill_manage` with validation and history.
- Middleware: compose memory updates, session indexing, loop detection, prompt caching, clarification, and tool error handling.
<!-- [DEPRECATED] sandbox configuration removed:
- Local sandbox mapping: run local file operations against controlled workspace paths; host bash is opt-in and should remain disabled for demos.
-->

## Project Structure

```text
.
|-- main.py                         # CLI entry point
|-- config.yaml                     # Private local config; ignored by git
|-- config.example.yaml             # Safe example config
|-- extensions_config.json          # Private MCP/skills config; ignored by git
|-- extensions_config.example.json  # Safe example extension config
|-- backend/                        # Core runtime, agents, tools, memory, config, sandbox
|-- skills/                         # Public and custom skills
|-- docs/                           # Demo and roadmap notes
|-- .agentflow/                     # Local runtime state; ignored by git
`-- ARCHITECTURE.md                 # High-level architecture
```

Runtime data is stored under `.agentflow/` by default. Set `AGENTFLOW_HOME` to use another location. `DEER_FLOW_HOME` is still accepted as a compatibility fallback for older local setups.

## Installation

```powershell
uv sync
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
Copy-Item extensions_config.example.json extensions_config.json
```

Fill in the API keys you actually use in `.env`. Do not commit `.env`, `config.yaml`, `extensions_config.json`, `.agentflow/`, logs, or database files.

## Configuration

Model profiles live in `config.yaml` and should reference environment variables:

```yaml
models:
  - name: deepseek-v4
    use: backend.models.patched_deepseek:PatchedChatDeepSeek
    model: deepseek-v4-pro
    api_key: $DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com
```

Tool access is controlled by groups:

```yaml
tool_groups:
  - name: web
  - name: file:read
  - name: file:write
  - name: bash
```

For a safe showcase configuration, keep host bash disabled:

<!-- [DEPRECATED] sandbox configuration removed:
```yaml
sandbox:
  use: sandbox.local:LocalSandboxProvider
  allow_host_bash: false
```

Enable host bash only in a fully trusted local environment.
-->

## Usage

Start the CLI:

```powershell
python main.py
```

The active custom agent is selected by `active_agent` in `config.yaml`. If the configured agent does not exist, AgentFlow creates a minimal agent directory under:

```text
.agentflow/agents/<agent-name>/
```

Each custom agent may contain:

```text
config.yaml    # Agent metadata, model overrides, tool groups, and skills
SOUL.md        # Agent role, mission, style, and boundaries
memory.json    # Agent-specific long-term memory
```

## Development

Run the core tests:

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Run import checks:

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -c "import client; import backend.client; print('ok')"
```

## Security Notes

This repository is designed to keep private state out of source control:

- `config.yaml`, `extensions_config.json`, `.env`, `.agentflow/`, `*.db`, and logs are ignored.
- Example files contain placeholders only.
- Real tokens must live in environment variables or private local config.
- Any API key, GitHub token, or LeetCode session that was ever committed, pasted into chat, or stored in a shared file should be rotated on the provider side.

## Interview Demo

See [docs/DEMO.md](docs/DEMO.md) for the recommended demo path: read code, fix a bug, run tests, capture a reusable skill, and recall context in a later session.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the next phase: evaluation harness, permission policy, memory/skill approval, and subagent reliability.

## License

See [LICENSE](LICENSE).
