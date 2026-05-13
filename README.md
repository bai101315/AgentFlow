# Agent_Base CLI

Agent_Base CLI is a local multi-agent command-line application built on LangGraph and LangChain. It supports custom agents with independent role definitions, model connections, session persistence, long-term memory, tools, and skills.

The project is designed for local experimentation and extensible agent workflows, including research assistants, coding assistants, learning companions, daily report agents, and other task-specific AI agents.

## Features

- Multi-agent CLI management: create, delete, switch, and chat with custom agents.
- Per-agent identity: each agent can have its own `SOUL.md` role and behavior definition.
- Per-agent model configuration: provider model, API key, and base URL can be configured during creation.
- Persistent sessions: each agent uses an independent thread ID and can continue previous conversations.
- Long-term memory: each agent can maintain its own `memory.json`.
- Tool system: configurable tool groups for web search, file access, file writing, and shell execution.
- Skill system: load reusable skills from the local `skills/` directory.
- MCP extension support: optional external tools can be configured through `extensions_config.json`.
- SQLite checkpoint support for local state persistence.

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

The project uses a workspace-style Python setup. The root package is defined in `pyproject.toml`, with backend logic under `backend/`.

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
git clone <your-repository-url>
cd <your-repository-name>
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
