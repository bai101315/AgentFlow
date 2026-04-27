import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from until import *

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

load_dotenv()

_LOG_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


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

    config = {
        "configurable": {
            "thread_id": "debug-thread-001",
            "thinking_enabled": False,
            "is_plan_mode": True,
            "model_name": "minimax-m2.5",
            "subagent_enabled": True,
            "tools_enabled": True,
        }
    }

    thread_id = config["configurable"]["thread_id"]
    runtime = Runtime(context={"thread_id": thread_id})
    config["configurable"]["__pregel_runtime"] = runtime

    session = PromptSession(history=InMemoryHistory()) if _HAS_PROMPT_TOOLKIT else None

    async with make_checkpointer() as checkpointer:
        agent = make_lead_agent(config, checkpointer=checkpointer)

        while True:
            try:
                if session:
                    user_input = (await session.prompt_async("\nYou: ")).strip()
                else:
                    user_input = input(f"{CYAN}{BOLD}You >> {RESET}").strip()

                if not user_input:
                    continue
                if user_input.lower() in ("q", "exit"):
                    print("Goodbye!")
                    break

                state = {"messages": [HumanMessage(content=user_input)]}
                result = await agent.ainvoke(state, config=config, context={"thread_id": thread_id})

                if result.get("messages"):
                    last_message = result["messages"][-1]
                    print(f"\n{GREEN}{BOLD}Agent{RESET}: {last_message.content}")
            except KeyboardInterrupt:
                print("Goodbye!")
                break
            except Exception as exc:
                print(f"\nError: {exc}")
                import traceback

                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
