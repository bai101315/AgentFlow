import asyncio
import logging
import re
import shutil
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
import yaml

from until import *

# try:
#     from prompt_toolkit import PromptSession
#     from prompt_toolkit.history import InMemoryHistory

#     _HAS_PROMPT_TOOLKIT = True
# except ImportError:
#     _HAS_PROMPT_TOOLKIT = False

load_dotenv()

_LOG_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_SOUL_MIN_CHARS = 600
_SOUL_REQUIRED_HEADERS = (
    "## Role",
    "## Mission",
    "## Communication Style",
    "## Emotional Support Strategy",
    "## Clarification Strategy",
    "## Output Preferences",
    "## Boundaries",
    "## Failure Handling",
    "## Continuous Improvement",
)


def _logging_level_from_config(name: str) -> int:
    """Map config log_level string to a logging level constant."""
    mapping = logging.getLevelNamesMapping()
    return mapping.get((name or "info").strip().upper(), logging.INFO)


def _setup_logging(log_level: str) -> None:
    """Send application logs to debug.log only (no console output)."""
    level = _logging_level_from_config(log_level)
    root = logging.root
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)

    file_handler = logging.FileHandler("debug.log", mode="a", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    root.addHandler(file_handler)


def _update_logging_level(log_level: str) -> None:
    """Update root logger and all handlers to log_level."""
    level = _logging_level_from_config(log_level)
    root = logging.root
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)


# Ensure local backend modules are importable when running from repo root.
BACKEND_ROOT = Path(__file__).resolve().parent / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _prompt_line(label: str) -> str:
    return input(f"{CYAN}{BOLD}{label}{RESET}").strip()


def _is_valid_url(url: str) -> bool:
    lowered = (url or "").strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


_MODEL_PROVIDER_OPTIONS = (
    {
        "label": "MINIMAX",
        "base_url": "https://api.minimaxi.com/v1",
        "models": ("MiniMax-M2.5", "MiniMax-M2.7"),
    },
    {
        "label": "Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ("qwen3.5-flash", "qwen-plus", "qwen-max"),
    },
    {
        "label": "DEEPSEEK",
        "base_url": "https://api.deepseek.com/v1",
        "models": ("deepseek-chat", "deepseek-reasoner"),
    },
    {
        "label": "OpenAI Compatible",
        "base_url": "",
        "models": (),
    },
)


def _choose_model_config() -> dict[str, str]:
    from backend.config import get_app_config

    app_config = get_app_config()
    base_profiles = app_config.models or []
    if not base_profiles:
        raise ValueError("No models available in global config.yaml.")
    base_profile = base_profiles[0].name

    while True:
        print("\nModel setup:")
        for idx, option in enumerate(_MODEL_PROVIDER_OPTIONS, 1):
            print(f"{idx}) {option['label']}")

        selected = _prompt_line("Select model provider: ")
        if not selected.isdigit():
            print("Invalid selection.")
            continue
        idx = int(selected)
        if idx < 1 or idx > len(_MODEL_PROVIDER_OPTIONS):
            print("Selection out of range.")
            continue

        option = _MODEL_PROVIDER_OPTIONS[idx - 1]
        default_base_url = option["base_url"]
        provider_model = ""
        models = option["models"]
        print(f"\n{option['label']} models:")
        if models:
            for model_idx, model_name in enumerate(models, 1):
                print(f"{model_idx}) {model_name}")
            model_selected = _prompt_line("Select model: ")
            if not model_selected.isdigit():
                print("Invalid selection.")
                continue
            model_idx = int(model_selected)
            if model_idx < 1 or model_idx > len(models):
                print("Selection out of range.")
                continue
            provider_model = models[model_idx - 1]
        else:
            print("1) Custom model name")
            custom_selected = _prompt_line("Select model: ")
            if custom_selected != "1":
                print("Invalid selection.")
                continue
            provider_model = _prompt_line("Provider model name: ")

        base_url_prompt = f"Base URL [{default_base_url}]: " if default_base_url else "Base URL (http/https): "
        api_key = _prompt_line("API key: ")
        base_url = _prompt_line(base_url_prompt) or default_base_url
        if not provider_model:
            print("Provider model cannot be empty.")
            continue
        if not api_key:
            print("API key cannot be empty.")
            continue
        if not _is_valid_url(base_url):
            print("Invalid base URL. Must start with http:// or https://")
            continue
        if option["label"] == "DEEPSEEK" and "/anthropic" in base_url.lower():
            print("Invalid DeepSeek base URL for this OpenAI-compatible profile. Use https://api.deepseek.com/v1")
            continue
        return {
            "model": base_profile,
            "provider_model": provider_model,
            "api_key": api_key,
            "base_url": base_url,
        }


def _print_main_menu(current_agent: str) -> None:
    print(f"\n{BOLD}=== Main Menu ==={RESET}")
    print("1) Enter Chat")
    print("2) Manage Agents")
    print("3) Exit")
    print(f"Current agent: {current_agent}")


def _list_custom_agents():
    from backend.config.agents_config import list_custom_agents

    return list_custom_agents()


def _last_agent_file() -> Path:
    from backend.config.paths import get_paths

    return get_paths().base_dir / "last_agent.txt"


def _save_last_agent(agent_name: str) -> None:
    try:
        _last_agent_file().write_text(agent_name, encoding="utf-8")
    except Exception:
        pass


def _agent_threads_file() -> Path:
    from backend.config.paths import get_paths

    return get_paths().base_dir / "agent_threads.yaml"


def _normalize_agent_name(agent_name: str | None) -> str:
    return (agent_name or "test").lower()


def _load_agent_threads_map() -> dict[str, str]:
    path = _agent_threads_file()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in data.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k.lower()] = v.strip()
        return out
    except Exception:
        return {}


def _save_agent_threads_map(data: dict[str, str]) -> None:
    path = _agent_threads_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=True)


def _ensure_agent_thread_id(agent_name: str | None) -> str:
    name = _normalize_agent_name(agent_name)
    data = _load_agent_threads_map()
    thread_id = data.get(name)
    if thread_id:
        return thread_id
    thread_id = f"{name}-{uuid.uuid4().hex[:8]}"
    data[name] = thread_id
    _save_agent_threads_map(data)
    return thread_id


def _ensure_agent_memory_file(agent_name: str) -> None:
    from backend.agents.memory.storage import create_empty_memory, get_memory_storage
    from backend.config.paths import get_paths

    if agent_name == "test":
        return
    memory_file = get_paths().agent_memory_file(agent_name)
    if memory_file.exists():
        return
    get_memory_storage().save(create_empty_memory(), agent_name)


def _load_last_agent(default_agent: str = "test") -> str:
    from backend.config.agents_config import AGENT_NAME_PATTERN
    from backend.config.paths import get_paths

    try:
        path = _last_agent_file()
        if not path.exists():
            return default_agent
        name = path.read_text(encoding="utf-8").strip().lower()
        if name == "test":
            return "test"
        if not name or not AGENT_NAME_PATTERN.match(name):
            return default_agent
        if not get_paths().agent_dir(name).exists():
            return default_agent
        return name
    except Exception:
        return default_agent


def _default_soul(name: str, description: str) -> str:
    role_line = description or "A helpful AI assistant."
    return (
        f"# {name} Soul\n\n"
        "## Role\n"
        f"You are `{name}`, an AI assistant focused on: {role_line}\n\n"
        "## Mission\n"
        "- Understand the user's real situation, not only the literal question.\n"
        "- Provide grounded help that can be executed immediately.\n"
        "- When user is stressed, first stabilize emotion, then move to action.\n\n"
        "## Communication Style\n"
        "- Keep the same language as the user.\n"
        "- Be clear, concise, and specific.\n"
        "- Use concrete observations from user messages rather than generic comfort.\n\n"
        "## Emotional Support Strategy\n"
        "- Follow a balanced order: empathy -> clarification -> solution.\n"
        "- Acknowledge feelings before giving advice.\n"
        "- Avoid empty slogans; offer practical next steps with warmth.\n\n"
        "## Clarification Strategy\n"
        "- Ask concise questions when requirements are ambiguous.\n"
        "- If user gives abstract goals, infer likely scenarios and confirm quickly.\n"
        "- Avoid long interrogations; clarify only what is necessary to proceed.\n\n"
        "## Output Preferences\n"
        "- Default structure: conclusion first, then actionable steps.\n"
        "- Prefer checklists only when tasks are multi-step.\n"
        "- Include examples when they reduce user effort.\n\n"
        "## Boundaries\n"
        "- Do not invent facts, metrics, or outcomes.\n"
        "- Do not dismiss user emotions.\n"
        "- Do not overpromise certainty for unknown situations.\n\n"
        "## Failure Handling\n"
        "- If blocked, explain the blocker and offer the best fallback path.\n"
        "- If previous response was off-target, acknowledge and correct directly.\n"
        "- Keep momentum with a smallest-next-step option.\n\n"
        "## Continuous Improvement\n"
        "- Learn from user corrections in the current and later sessions.\n"
        "- Track user preferred response style and keep it stable.\n"
        "- Prefer progressively better answers over defensive explanations.\n"
    )


def _looks_like_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _validate_soul_quality(text: str) -> bool:
    if not text or len(text.strip()) < _SOUL_MIN_CHARS:
        return False
    return all(header in text for header in _SOUL_REQUIRED_HEADERS)


def _build_soul_generation_prompt(agent_name: str, description: str) -> str:
    language_hint = "Chinese" if _looks_like_chinese(description + agent_name) else "English"
    return f"""
You are generating a SOUL.md for a custom AI agent.

Output requirements:
1. Return only Markdown content.
2. Use language: {language_hint}.
3. Must include ALL sections exactly:
   - # {agent_name} Soul
   - ## Role
   - ## Mission
   - ## Communication Style
   - ## Emotional Support Strategy
   - ## Clarification Strategy
   - ## Output Preferences
   - ## Boundaries
   - ## Failure Handling
   - ## Continuous Improvement
4. Be specific and actionable, not generic.
5. Default interaction rhythm: empathy -> clarification -> solution.
6. For emotional cases: acknowledge feelings first, then practical steps.
7. For abstract user input: infer likely scenarios (study/work/stress) and provide robust behavior guidance.

Agent name: {agent_name}
User one-line requirement: {description or "A helpful assistant for mixed practical and emotional support"}
""".strip()


def _extract_model_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts).strip()
    return str(content).strip()


def _sanitize_generated_soul(text: str) -> str:
    # Remove model thinking blocks and keep markdown body only.
    cleaned = text.strip()
    cleaned = cleaned.replace("\r\n", "\n")
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE).strip()
    hash_idx = cleaned.find("# ")
    if hash_idx > 0:
        cleaned = cleaned[hash_idx:].strip()
    return cleaned


def _is_soul_meta_leak(text: str) -> bool:
    lowered = text.lower()
    bad_markers = (
        "you are generating a soul.md",
        "output requirements",
        "the user wants me to generate",
        "let me analyze the requirements",
        "i need to create a comprehensive soul",
    )
    return any(marker in lowered for marker in bad_markers)


def _generate_soul_with_model(agent_name: str, description: str) -> str | None:
    try:
        from models import create_chat_model

        model = create_chat_model(thinking_enabled=False)
        prompt = _build_soul_generation_prompt(agent_name, description)
        response = model.invoke(prompt)
        text = _sanitize_generated_soul(_extract_model_text(response.content))
        return text or None
    except Exception:
        return None


def generate_soul(agent_name: str, description: str) -> str:
    generated = _generate_soul_with_model(agent_name, description)
    if generated and (not _is_soul_meta_leak(generated)) and _validate_soul_quality(generated):
        return generated
    return _default_soul(agent_name, description)


def _create_agent_interactive() -> str | None:
    from backend.config.agents_config import AGENT_NAME_PATTERN
    from backend.config.paths import get_paths

    raw_name = _prompt_line("Agent name (letters/numbers/hyphen): ")
    if not raw_name:
        print("Cancelled.")
        return None

    name = raw_name.lower()
    if not AGENT_NAME_PATTERN.match(name):
        print("Invalid name. Use only letters, numbers, and hyphens.")
        return None

    description = _prompt_line("Agent function (one line): ")
    model_config = _choose_model_config()
    paths = get_paths()
    agent_dir = paths.agent_dir(name)
    if agent_dir.exists():
        print(f"Agent '{name}' already exists.")
        return None

    agent_dir.mkdir(parents=True, exist_ok=False)
    config_data = {"name": name, "description": description, **model_config}
    config_file = agent_dir / "config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f, allow_unicode=True, sort_keys=False)

    soul_file = agent_dir / "SOUL.md"
    soul_file.write_text(generate_soul(name, description), encoding="utf-8")
    _ensure_agent_thread_id(name)
    print(f"Agent '{name}' created.")
    return name


def _delete_agent_interactive(current_agent: str) -> str:
    from backend.config.paths import get_paths

    agents = _list_custom_agents()
    if not agents:
        print("No custom agents to delete.")
        return current_agent

    print("\nCustom agents:")
    for idx, agent in enumerate(agents, 1):
        marker = " (current)" if agent.name == current_agent else ""
        print(f"{idx}) {agent.name} - {agent.description}{marker}")

    raw_idx = _prompt_line("Select number to delete (blank to cancel): ")
    if not raw_idx:
        print("Cancelled.")
        return current_agent
    if not raw_idx.isdigit():
        print("Invalid selection.")
        return current_agent

    idx = int(raw_idx)
    if idx < 1 or idx > len(agents):
        print("Selection out of range.")
        return current_agent

    target = agents[idx - 1].name
    confirm = _prompt_line(f"Type YES to permanently delete '{target}': ")
    if confirm != "YES":
        print("Cancelled.")
        return current_agent

    agent_dir = get_paths().agent_dir(target)
    if not agent_dir.exists():
        print("Agent directory does not exist anymore.")
        return current_agent

    shutil.rmtree(agent_dir)
    print(f"Agent '{target}' deleted.")
    if current_agent == target:
        print("Current agent was deleted. Switched to 'test'.")
        return "test"
    return current_agent


def _switch_agent_interactive(current_agent: str) -> str | None:
    agents = _list_custom_agents()
    print("\nSwitch agent:")
    print(f"0) test{' (current)' if current_agent == 'test' else ''}")
    for idx, agent in enumerate(agents, 1):
        marker = " (current)" if agent.name == current_agent else ""
        print(f"{idx}) {agent.name}{marker}")

    raw_idx = _prompt_line("Select number (blank to cancel): ")
    if not raw_idx:
        print("Cancelled.")
        return None
    if not raw_idx.isdigit():
        print("Invalid selection.")
        return None

    idx = int(raw_idx)
    if idx == 0:
        print("Switched to 'test'.")
        return "test"
    if idx < 1 or idx > len(agents):
        print("Selection out of range.")
        return None

    selected = agents[idx - 1].name
    print(f"Switched to '{selected}'.")
    return selected


def _manage_agents_menu(current_agent: str) -> tuple[str, bool]:
    while True:
        print(f"\n{BOLD}=== Manage Agents ==={RESET}")
        agents = _list_custom_agents()
        if agents:
            print("Custom agents:")
            for agent in agents:
                marker = " (current)" if agent.name == current_agent else ""
                print(f"- {agent.name}: {agent.description}{marker}")
        else:
            print("Custom agents: (none)")
        print(f"Current agent: {current_agent}")
        print("1) Create agent")
        print("2) Delete agent")
        print("3) Switch current agent")
        print("4) Back")

        choice = _prompt_line("Choice: ")
        if choice == "1":
            created = _create_agent_interactive()
            if created:
                return created, True
        elif choice == "2":
            current_agent = _delete_agent_interactive(current_agent)
        elif choice == "3":
            selected = _switch_agent_interactive(current_agent)
            if selected:
                return selected, True
        elif choice == "4":
            return current_agent, False
        else:
            print("Invalid choice.")


async def main() -> None:
    # Install file logging first so import-time warnings do not leak to console.
    _setup_logging("info")

    from langchain_core.messages import HumanMessage
    from langgraph.runtime import Runtime

    from backend.agents import make_lead_agent
    from backend.agents.checkpointer import make_checkpointer
    from backend.config import get_app_config
    from backend.deer_flow_mcp import initialize_mcp_tools

    app_config = get_app_config()
    _update_logging_level(app_config.log_level)

    try:
        await initialize_mcp_tools()
    except Exception as exc:
        print(f"Warning: Failed to initialize MCP tools: {exc}")

    initial_agent = _load_last_agent(default_agent="test")
    initial_thread_id = _ensure_agent_thread_id(initial_agent)

    config = {
        "configurable": {
            "thread_id": initial_thread_id,
            "thinking_enabled": False,
            "is_plan_mode": True,
            "model_name": "minimax-m2.5",
            "subagent_enabled": True,
            "tools_enabled": True,
            "agent_name": initial_agent,
        }
    }


    async with make_checkpointer() as checkpointer:
        current_agent = config["configurable"].get("agent_name", "test")
        running = True

        while running:
            try:
                _print_main_menu(current_agent)
                top_choice = _prompt_line("Choice: ")

                if top_choice == "3":
                    print("Goodbye!")
                    break
                if top_choice == "2":
                    current_agent, should_enter_chat = _manage_agents_menu(current_agent)
                    config["configurable"]["agent_name"] = current_agent
                    _save_last_agent(current_agent)
                    if not should_enter_chat:
                        continue
                elif top_choice != "1":
                    print("Invalid choice.")
                    continue

                config["configurable"]["agent_name"] = current_agent
                current_thread_id = _ensure_agent_thread_id(current_agent)
                config["configurable"]["thread_id"] = current_thread_id
                runtime = Runtime(context={"thread_id": current_thread_id})
                config["configurable"]["__pregel_runtime"] = runtime
                _save_last_agent(current_agent)
                _ensure_agent_memory_file(current_agent)
                agent = make_lead_agent(config, checkpointer=checkpointer)
                print("Chat started. Type '/menu' to return, or 'exit' to quit.")

                while True:
     
                    user_input = _prompt_line("You >> ")

                    if not user_input:
                        continue
                    if user_input.lower() in ("q", "exit"):
                        print("Goodbye!")
                        running = False
                        break
                    if user_input == "/menu":
                        break

                    state = {"messages": [HumanMessage(content=user_input)]}
                    result = await agent.ainvoke(state, config=config, context={"thread_id": current_thread_id})

                    if result.get("messages"):
                        last_message = result["messages"][-1]
                        print(f"\n{GREEN}{BOLD}{current_agent}{RESET}: {last_message.content}")

            except KeyboardInterrupt:
                print("Goodbye!")
                break
            except Exception as exc:
                print(f"\nError: {exc}")
                import traceback

                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
