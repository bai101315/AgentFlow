# AgentFlow

AgentFlow is a local multi-agent orchestration and governance framework built on LangGraph and LangChain.

It focuses on runtime-level concerns that appear when multiple agents run in the same local environment: tool context control, middleware governance, memory persistence, session isolation, local sandbox execution, MCP tool integration, and customizable agent behavior.

The goal is not only to provide a chat interface, but to provide a configurable Agent Runtime for building task-specific agents with independent role definitions, model connections, memory spaces, tool permissions, and skills.

## Design Goals

AgentFlow is designed around several runtime-level problems:

- Prevent context pollution between multiple agents running in the same process.
- Avoid exposing all tools to every agent by default.
- Keep long-running conversations resumable through checkpointed session state.
- Preserve useful long-term user and task context through per-agent memory.
- Keep MCP and local tools controllable as the tool surface grows.
- Provide a local execution model that is practical on Windows while still enforcing path and permission boundaries.

## Features

- Agent lifecycle management: create, delete, switch, and resume custom agents.
- Runtime context isolation: each agent can maintain independent role definition, session thread, memory file, and workspace.
- Agent-level model binding: configure provider model, API key, and base URL per agent.
- Tool governance: expose tools by configurable groups such as web, file read, file write, and bash.
- Deferred tool discovery: integrate MCP tools through a delayed `tool_search` mechanism to reduce prompt context overhead.
- Middleware governance: support summarization, memory update, loop detection, clarification, and tool error handling.
- Long-term memory and session persistence: combine per-agent memory with SQLite checkpointing.
- Skill injection: load reusable skills from local public/custom skill directories.

## Tech Stack

- Python 3.12+
- LangGraph / LangGraph SDK
- LangChain
- FastAPI
- Pydantic
- PyYAML
- python-dotenv
- aiosqlite
- uv

The project uses a workspace-style Python setup. The root package is defined in `pyproject.toml`, with core runtime logic under `backend/`.

## Project Structure

```text
.
|-- main.py                  # CLI entry point
|-- config.yaml              # Global model, tool, memory, sandbox, and checkpoint config
|-- extensions_config.json   # MCP servers and skill extension config
|-- backend/                 # Core agent, model, tool, memory, config, and sandbox logic
|-- skills/                  # Local skill definitions
|-- .deer_flow/              # Local runtime state, custom agents, memories, and threads
|-- pyproject.toml           # Project metadata and dependencies
`-- README.md
```

Runtime data is stored under `.deer_flow/` by default. This directory may contain local memories, checkpoint data, thread workspaces, and custom agent configuration.

## Installation

Clone the repository:

```bash
git@github.com:bai101315/LeetCode-Assistant.git
cd LeetCode-Assistant
```

Create and install the Python environment with `uv`:

```bash
uv sync
```

Alternatively, use your own Python 3.12+ environment and install the dependencies declared in `pyproject.toml`.

## Configuration

### Environment Variables

Create a `.env` file for sensitive values:

```env
DEEPSEEK_API_KEY=your_deepseek_api_key
TAVILY_API_KEY=your_tavily_api_key
```

Do not commit `.env` or real API keys to GitHub.

### Global Model Configuration

Global model profiles are defined in `config.yaml`:

```yaml
models:
  - name: example-model
    display_name: Example Model
    use: langchain_openai:ChatOpenAI
    model: example-provider-model
    api_key: $EXAMPLE_API_KEY
    base_url: https://api.example.com/v1
    max_tokens: 4096
    temperature: 0.7
    supports_thinking: false
    supports_vision: false
```

Custom agents can also override the provider model, API key, and base URL in their own configuration files.

### Tools

Tools are configured in `config.yaml` using tool groups:

```yaml
tool_groups:
  - name: web
  - name: file:read
  - name: file:write
  - name: bash
```

Each agent can restrict its available tools by setting `tool_groups` in its own `config.yaml`.

### Memory and Checkpointing

Long-term memory and session persistence are configured in `config.yaml`:

```yaml
memory:
  enabled: true
  storage_path: memory.json
  debounce_seconds: 30
  injection_enabled: true

checkpointer:
  type: sqlite
  connection_string: checkpoints.db
```

Each custom agent can maintain its own memory file under `.deer_flow/agents/<agent-name>/memory.json`.

### Skills

Skills are loaded from the local `skills/` directory:

```yaml
skills:
  path: ./skills
  container_path: ../skills
```

Agent-specific skills can be controlled in the agent configuration:

```yaml
skills:
  - example-skill
```

If the `skills` field is omitted, enabled skills are loaded according to the extension configuration.

## Usage

Start the CLI:

```bash
python main.py
```

The application opens a main menu:

```text
1) Enter Chat
2) Manage Agents
3) Exit
```

From the agent management menu, you can:

- Create a new agent.
- Delete an existing custom agent.
- Switch the current agent.

During chat, type `/menu` to return to the main menu, or `exit` / `q` to quit.

## Custom Agents

Custom agents are stored under:

```text
.deer_flow/agents/<agent-name>/
```

Each custom agent may contain:

```text
config.yaml    # Agent metadata, model overrides, tools, and skills
SOUL.md        # Agent role, mission, communication style, and boundaries
memory.json    # Agent-specific long-term memory
```

Example agent configuration:

```yaml
name: daily-report
description: AI community daily report assistant
model: example-model
provider_model: example-provider-model
api_key: $EXAMPLE_API_KEY
base_url: https://api.example.com/v1
tool_groups:
  - web
  - file:read
skills:
  - report-writing
```

## Security Notes

Before publishing this project to GitHub, review and remove sensitive local data:

- API keys in `config.yaml`.
- Tokens or sessions in `extensions_config.json`.
- `.env` files.
- `.deer_flow/` runtime data.
- `debug.log`.
- Local checkpoint databases such as `checkpoints.db`.

It is recommended to provide a sanitized example config, such as `config.example.yaml`, and keep private configuration files out of version control.

## Development

Run code formatting or linting according to the project configuration:

```bash
uv run ruff check .
```

The development dependency group is declared in `pyproject.toml`.

## License

This project includes a `LICENSE` file. See it for licensing details.
