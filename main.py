import asyncio
import logging
import re
import sys
import uuid
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import json

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
_DEFAULT_AGENT_ALIASES = {"", "default", "test", "none", "null"}


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


def _last_agent_file() -> Path:
    from config.paths import get_paths

    return get_paths().base_dir / "last_agent.txt"


def _save_last_agent(agent_name: str) -> None:
    try:
        _last_agent_file().write_text(agent_name, encoding="utf-8")
    except Exception:
        pass


def _agent_threads_file() -> Path:
    from config.paths import get_paths

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
    from agents.memory.storage import create_empty_memory, get_memory_storage
    from config.paths import get_paths

    if agent_name == "test":
        return
    memory_file = get_paths().agent_memory_file(agent_name)
    if memory_file.exists():
        return
    get_memory_storage().save(create_empty_memory(), agent_name)


def _load_last_agent(default_agent: str = "test") -> str:
    from config.agents_config import AGENT_NAME_PATTERN
    from config.paths import get_paths

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


def _app_config_path() -> Path:
    from config.app_config import AppConfig

    return AppConfig.resolve_config_path()


def _file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _get_config_agent_name(app_config) -> str:
    configured = None
    for field_name in ("active_agent", "agent_name", "current_agent"):
        if hasattr(app_config, field_name):
            configured = getattr(app_config, field_name)
            break
    if configured is None:
        return _load_last_agent(default_agent="test")

    name = str(configured or "").strip().lower()
    if name in _DEFAULT_AGENT_ALIASES:
        return "test"
    return name


def _ensure_config_agent(agent_name: str) -> str:
    from config.agents_config import AGENT_NAME_PATTERN
    from config.paths import get_paths

    name = (agent_name or "test").strip().lower()
    if name in _DEFAULT_AGENT_ALIASES:
        return "test"
    if not AGENT_NAME_PATTERN.match(name):
        print(f"Warning: Invalid agent name '{agent_name}', using 'test'.")
        return "test"
    if name == "test":
        return name

    paths = get_paths()
    agent_dir = paths.agent_dir(name)
    if agent_dir.exists():
        return name

    agent_dir.mkdir(parents=True, exist_ok=False)
    description = f"Custom agent '{name}'."
    config_data = {"name": name, "description": description}
    config_file = agent_dir / "config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f, allow_unicode=True, sort_keys=False)

    soul_file = agent_dir / "SOUL.md"
    soul_file.write_text(generate_soul(name, description), encoding="utf-8")
    _ensure_agent_thread_id(name)
    print(f"Created agent '{name}' from config.")
    return name


def _agent_config_files(agent_name: str) -> tuple[Path, ...]:
    from config.paths import get_paths

    if agent_name == "test":
        return ()
    agent_dir = get_paths().agent_dir(agent_name)
    return (agent_dir / "config.yaml", agent_dir / "SOUL.md")


def _runtime_config_signature(agent_name: str) -> tuple:
    paths = [_app_config_path(), *_agent_config_files(agent_name)]
    return tuple((str(path), _file_mtime(path)) for path in paths)


def _build_runtime_config(agent_name: str, thread_id: str) -> dict:
    runtime_agent_name = None if agent_name == "test" else agent_name
    return {
        "configurable": {
            "thread_id": thread_id,
            "thinking_enabled": False,
            "is_plan_mode": True,
            # Original runtime default kept for reference:
            # "model_name": None,
            "model_name": "deepseek-v4",
            "subagent_enabled": True,
            "tools_enabled": True,
            "agent_name": runtime_agent_name,
        }
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _session_logs_dir() -> Path:
    from config.paths import get_paths

    return get_paths().base_dir / "session_logs"


def _extract_usage_stats(message: Any) -> dict[str, Any]:
    usage = getattr(message, "usage_metadata", None) or {}
    response_metadata = getattr(message, "response_metadata", None) or {}
    if not usage and isinstance(response_metadata, dict):
        usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    input_details = usage.get("input_token_details") or {}
    if not isinstance(input_details, dict):
        input_details = {}

    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or (input_tokens + output_tokens))
    cache_read_tokens = int(usage.get("cache_read_input_tokens", input_details.get("cache_read", 0)) or 0)
    cache_creation_tokens = int(
        usage.get("cache_creation_input_tokens", input_details.get("cache_creation", 0)) or 0
    )
    prompt_cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens", cache_read_tokens) or 0)
    prompt_cache_miss_tokens = int(
        usage.get("prompt_cache_miss_tokens", max(0, input_tokens - prompt_cache_hit_tokens)) or 0
    )
    cache_denominator = prompt_cache_hit_tokens + prompt_cache_miss_tokens
    prompt_cache_hit_rate = (
        prompt_cache_hit_tokens / cache_denominator if cache_denominator > 0 else None
    )
    billable_input_tokens = (
        prompt_cache_miss_tokens
        if prompt_cache_miss_tokens > 0
        else max(0, input_tokens - prompt_cache_hit_tokens)
    )

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "billable_input_tokens": billable_input_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": cache_creation_tokens,
        "prompt_cache_hit_tokens": prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": prompt_cache_miss_tokens,
        "prompt_cache_hit_rate": prompt_cache_hit_rate,
        "cache_signal_available": cache_denominator > 0 or cache_read_tokens > 0 or cache_creation_tokens > 0,
    }


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


@dataclass
class SessionTurnRecord:
    turn_index: int
    timestamp: str
    agent_name: str
    thread_id: str
    model_name: str | None
    user_input: str
    assistant_output: str
    usage: dict[str, Any]


class SessionRecorder:
    def __init__(self, *, agent_name: str, thread_id: str, model_name: str | None) -> None:
        started_at = _utc_now_iso()
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", agent_name or "default").strip("-") or "default"
        self.session_id = f"{slug}-{uuid.uuid4().hex[:8]}"
        self.path = _session_logs_dir() / f"{started_at[:19].replace(':', '-')}-{self.session_id}.json"
        self.started_at = started_at
        self.initial_agent_name = agent_name
        self.initial_thread_id = thread_id
        self.initial_model_name = model_name
        self.turns: list[SessionTurnRecord] = []
        self.ended_at: str | None = None
        self.exit_reason: str | None = None
        self._save()

    def record_turn(
        self,
        *,
        agent_name: str,
        thread_id: str,
        model_name: str | None,
        user_input: str,
        assistant_output: str,
        usage: dict[str, Any],
    ) -> None:
        self.turns.append(
            SessionTurnRecord(
                turn_index=len(self.turns) + 1,
                timestamp=_utc_now_iso(),
                agent_name=agent_name,
                thread_id=thread_id,
                model_name=model_name,
                user_input=user_input,
                assistant_output=assistant_output,
                usage=usage,
            )
        )
        self._save()

    def finalize(self, exit_reason: str) -> dict[str, Any]:
        self.ended_at = _utc_now_iso()
        self.exit_reason = exit_reason
        summary = self._build_summary()
        self._save(summary=summary)
        return summary

    def _build_summary(self) -> dict[str, Any]:
        total_input_tokens = sum(turn.usage["input_tokens"] for turn in self.turns)
        total_output_tokens = sum(turn.usage["output_tokens"] for turn in self.turns)
        total_tokens = sum(turn.usage["total_tokens"] for turn in self.turns)
        total_billable_input_tokens = sum(turn.usage["billable_input_tokens"] for turn in self.turns)
        total_cache_hit_tokens = sum(turn.usage["prompt_cache_hit_tokens"] for turn in self.turns)
        total_cache_miss_tokens = sum(turn.usage["prompt_cache_miss_tokens"] for turn in self.turns)
        cache_denominator = total_cache_hit_tokens + total_cache_miss_tokens
        weighted_hit_rate = total_cache_hit_tokens / cache_denominator if cache_denominator > 0 else None
        per_turn_hit_rates = [
            turn.usage["prompt_cache_hit_rate"]
            for turn in self.turns
            if turn.usage["prompt_cache_hit_rate"] is not None
        ]
        average_hit_rate = (
            sum(per_turn_hit_rates) / len(per_turn_hit_rates) if per_turn_hit_rates else None
        )

        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_reason": self.exit_reason,
            "turn_count": len(self.turns),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "total_billable_input_tokens": total_billable_input_tokens,
            "total_prompt_cache_hit_tokens": total_cache_hit_tokens,
            "total_prompt_cache_miss_tokens": total_cache_miss_tokens,
            "prompt_cache_hit_rate": weighted_hit_rate,
            "average_turn_prompt_cache_hit_rate": average_hit_rate,
            "turns_with_cache_signal": len(per_turn_hit_rates),
        }

    def _to_dict(self, summary: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_reason": self.exit_reason,
            "initial_agent_name": self.initial_agent_name,
            "initial_thread_id": self.initial_thread_id,
            "initial_model_name": self.initial_model_name,
            "turns": [
                {
                    "turn_index": turn.turn_index,
                    "timestamp": turn.timestamp,
                    "agent_name": turn.agent_name,
                    "thread_id": turn.thread_id,
                    "model_name": turn.model_name,
                    "user_input": turn.user_input,
                    "assistant_output": turn.assistant_output,
                    "usage": turn.usage,
                }
                for turn in self.turns
            ],
            "summary": summary,
        }

    def _save(self, summary: dict[str, Any] | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._to_dict(summary=summary), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


async def main() -> None:
    startup_started_at = perf_counter()
    startup_timings: list[tuple[str, float]] = []

    def record_startup_timing(label: str, started_at: float) -> None:
        startup_timings.append((label, perf_counter() - started_at))

    # Install file logging first so import-time warnings do not leak to console.
    phase_started_at = perf_counter()
    _setup_logging("info")
    record_startup_timing("logging", phase_started_at)

    phase_started_at = perf_counter()
    from langchain_core.messages import HumanMessage
    from langgraph.runtime import Runtime

    from agents import make_lead_agent
    from agents.checkpointer import make_checkpointer
    from config import get_app_config
    from config.app_config import reload_app_config
    from deer_flow_mcp import initialize_mcp_tools
    record_startup_timing("imports", phase_started_at)

    phase_started_at = perf_counter()
    app_config = get_app_config()
    _update_logging_level(app_config.log_level)
    record_startup_timing("config_load", phase_started_at)

    phase_started_at = perf_counter()
    try:
        await initialize_mcp_tools()
    except Exception as exc:
        print(f"Warning: Failed to initialize MCP tools: {exc}")
    record_startup_timing("mcp_init", phase_started_at)

    phase_started_at = perf_counter()
    async with make_checkpointer() as checkpointer:
        record_startup_timing("checkpointer", phase_started_at)
        current_agent = ""
        current_thread_id = ""
        config = {}
        runtime_signature = None
        agent = None
        session_recorder: SessionRecorder | None = None

        def _current_model_name() -> str | None:
            return config.get("configurable", {}).get("model_name") or config.get("configurable", {}).get("model")

        def _print_session_summary(summary: dict[str, Any]) -> None:
            print("\nSession summary:")
            print(f"  turns: {summary['turn_count']}")
            print(f"  total tokens: {summary['total_tokens']}")
            print(f"  input/output: {summary['total_input_tokens']}/{summary['total_output_tokens']}")
            print(f"  billable input tokens: {summary['total_billable_input_tokens']}")
            print(
                "  prompt cache hit/miss: "
                f"{summary['total_prompt_cache_hit_tokens']}/{summary['total_prompt_cache_miss_tokens']}"
            )
            print(f"  prompt cache hit rate: {_format_ratio(summary['prompt_cache_hit_rate'])}")
            print(
                "  average per-turn hit rate: "
                f"{_format_ratio(summary['average_turn_prompt_cache_hit_rate'])}"
            )
            print(f"  saved to: {session_recorder.path if session_recorder else 'n/a'}")

        def _finalize_session(exit_reason: str) -> None:
            if session_recorder is None:
                return
            summary = session_recorder.finalize(exit_reason)
            _print_session_summary(summary)

        def rebuild_agent() -> tuple[float, list[tuple[str, float]]]:
            nonlocal app_config, current_agent, current_thread_id, config, runtime_signature, agent, session_recorder

            rebuild_started_at = perf_counter()
            rebuild_timings: list[tuple[str, float]] = []

            def record_rebuild_timing(label: str, started_at: float) -> None:
                rebuild_timings.append((label, perf_counter() - started_at))

            phase_started_at = perf_counter()
            app_config = reload_app_config()
            _update_logging_level(app_config.log_level)
            record_rebuild_timing("reload_config", phase_started_at)

            phase_started_at = perf_counter()
            selected_agent = _ensure_config_agent(_get_config_agent_name(app_config))
            selected_thread_id = _ensure_agent_thread_id(selected_agent)
            config = _build_runtime_config(selected_agent, selected_thread_id)
            runtime_agent_name = config["configurable"].get("agent_name")
            runtime = Runtime(context={"thread_id": selected_thread_id, "agent_name": runtime_agent_name})
            config["configurable"]["__pregel_runtime"] = runtime
            _save_last_agent(selected_agent)
            _ensure_agent_memory_file(selected_agent)
            record_rebuild_timing("agent_config", phase_started_at)

            phase_started_at = perf_counter()
            agent = make_lead_agent(config, checkpointer=checkpointer)
            record_rebuild_timing("make_lead_agent", phase_started_at)

            phase_started_at = perf_counter()
            current_agent = selected_agent
            current_thread_id = selected_thread_id
            runtime_signature = _runtime_config_signature(selected_agent)
            if session_recorder is None:
                session_recorder = SessionRecorder(
                    agent_name=selected_agent,
                    thread_id=selected_thread_id,
                    model_name=_current_model_name(),
                )
            record_rebuild_timing("session_recorder", phase_started_at)
            return perf_counter() - rebuild_started_at, rebuild_timings

        initial_agent_build_seconds, initial_agent_build_timings = rebuild_agent()
        startup_ready_seconds = perf_counter() - startup_started_at
        print(f"Chat started with agent '{current_agent}'. Type 'exit' or 'q' to quit.")
        print(
            "Startup ready: "
            f"{startup_ready_seconds:.3f}s total, "
            f"{initial_agent_build_seconds:.3f}s agent build."
        )
        timing_parts = [f"{label}={seconds:.3f}s" for label, seconds in startup_timings]
        timing_parts.extend(f"agent.{label}={seconds:.3f}s" for label, seconds in initial_agent_build_timings)
        print("Startup breakdown: " + ", ".join(timing_parts))

        while True:
            try:
                latest_app_config = get_app_config()
                selected_agent = _ensure_config_agent(_get_config_agent_name(latest_app_config))
                latest_signature = _runtime_config_signature(selected_agent)
                if selected_agent != current_agent or latest_signature != runtime_signature:
                    rebuild_seconds, rebuild_timings = rebuild_agent()
                    print(f"\nSwitched to agent '{current_agent}' from config ({rebuild_seconds:.3f}s rebuild).")
                    print(
                        "Rebuild breakdown: "
                        + ", ".join(f"{label}={seconds:.3f}s" for label, seconds in rebuild_timings)
                    )

                user_input = _prompt_line("You >> ")

                if not user_input:
                    continue
                if user_input.lower() in ("q", "exit"):
                    _finalize_session("user_exit")
                    print("Goodbye!")
                    break

                state = {"messages": [HumanMessage(content=user_input)]}
                result = await agent.ainvoke(
                    state,
                    config=config,
                    context={
                        "thread_id": current_thread_id,
                        "agent_name": config["configurable"].get("agent_name"),
                    },
                )

                if result.get("messages"):
                    last_message = result["messages"][-1]
                    print(f"\n{GREEN}{BOLD}{current_agent}{RESET}: {last_message.content}")
                    if session_recorder is not None:
                        session_recorder.record_turn(
                            agent_name=current_agent,
                            thread_id=current_thread_id,
                            model_name=_current_model_name(),
                            user_input=user_input,
                            assistant_output=str(last_message.content),
                            usage=_extract_usage_stats(last_message),
                        )

            except KeyboardInterrupt:
                _finalize_session("keyboard_interrupt")
                print("Goodbye!")
                break
            except Exception as exc:
                print(f"\nError: {exc}")
                import traceback

                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
