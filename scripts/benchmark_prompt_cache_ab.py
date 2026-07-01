"""Run a real A/B benchmark for prompt caching with one controlled variable.

This benchmark measures the provider-reported cache usage on the second turn
after a simulated restart. Both arms use:

- the same model
- the same tools
- the same middleware chain (except prompt-cache wiring)
- the same thread history
- the same external manual memory mutation between turns

The only variable is whether the second agent instance reuses the frozen system
prompt from checkpoint state.

Important: this measures the cache signal the provider returns in usage fields
such as ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``. It does not
claim access to the provider's raw internal KV pages.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import sys
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime as real_datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import sitecustomize  # noqa: F401,E402

from agents.lead_agent.agent import (  # noqa: E402
    _build_middlewares,
    _make_prompt_cache_middleware,
    _resolve_model_name,
)
from agents.lead_agent.prompt import build_session_prompt, warm_enabled_skills_cache  # noqa: E402
from agents.memory.storage import create_empty_memory, get_memory_storage, utc_now_iso_z  # noqa: E402
from agents.middlewares.prompt_cache_middleware import PROMPT_CACHE_PLACEHOLDER  # noqa: E402
from agents.middlewares.memory_middleware import MemoryMiddleware  # noqa: E402
from agents.thread_state import ThreadState  # noqa: E402
from config.agents_config import load_agent_config  # noqa: E402
from config.app_config import reload_app_config  # noqa: E402
from langchain.agents import create_agent  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.runtime import Runtime  # noqa: E402
from models import create_chat_model  # noqa: E402
from tools import get_available_tools  # noqa: E402

_FIXED_PROMPT_TIME = real_datetime(2026, 1, 1, 9, 0, 0)


def _make_large_memory_text(label: str, blocks: int = 96) -> str:
    parts: list[str] = []
    for index in range(blocks):
        parts.append(
            f"{label}-block-{index:03d}: "
            f"profile={label}; "
            f"topic=prompt-caching-benchmark; "
            f"evidence_marker={label.upper()}_{index:03d}; "
            f"payload={'|'.join([label] * 8)}"
        )
    return "\n".join(parts)


_MEMORY_TEXT_A = _make_large_memory_text("alpha")
_MEMORY_TEXT_B = _make_large_memory_text("bravo")


def _build_runtime_config(
    agent_name: str | None,
    thread_id: str,
    model_name: str,
) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
            "thinking_enabled": False,
            "is_plan_mode": True,
            "model_name": model_name,
            "subagent_enabled": True,
            "tools_enabled": True,
            "title_enabled": False,
            "agent_name": agent_name,
        }
    }


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _hash_prompt(prompt: str) -> tuple[str, int]:
    encoded = prompt.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16], len(encoded)


@contextmanager
def _patched_prompt_clock():
    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return _FIXED_PROMPT_TIME
            return _FIXED_PROMPT_TIME.astimezone(tz)

    with patch("agents.lead_agent.prompt.datetime", FrozenDateTime):
        yield


@dataclass(frozen=True)
class PromptRecord:
    phase: str
    sha256: str
    prompt_bytes: int


@dataclass(frozen=True)
class MemoryRecord:
    phase: str
    sha256: str
    memory_bytes: int
    fact_count: int
    first_fact_preview: str


@dataclass(frozen=True)
class TurnUsage:
    turn: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int

    @property
    def billable_input_tokens(self) -> int:
        if self.prompt_cache_miss_tokens > 0:
            return self.prompt_cache_miss_tokens
        if self.prompt_cache_hit_tokens > 0:
            return max(0, self.input_tokens - self.prompt_cache_hit_tokens)
        return self.input_tokens


@dataclass(frozen=True)
class RunResult:
    mode: str
    thread_id: str
    agent_name: str | None
    model_name: str
    initial_memory: MemoryRecord
    mutated_memory: MemoryRecord
    initial_prompt: PromptRecord
    restart_prompt: PromptRecord
    turn1: TurnUsage
    turn2: TurnUsage


@dataclass(frozen=True)
class SharedRuntime:
    agent_name: str | None
    model_name: str
    agent_config: Any
    model_config: Any
    model_overrides: dict[str, Any]
    tools: list[Any]
    subagent_enabled: bool
    max_concurrent_subagents: int


def _extract_usage(message: Any) -> TurnUsage:
    usage = getattr(message, "usage_metadata", None) or {}
    response_metadata = getattr(message, "response_metadata", None) or {}
    if not usage and isinstance(response_metadata, dict):
        usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    details = usage.get("input_token_details") or {}
    if not isinstance(details, dict):
        details = {}

    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", 0) or 0)
    cache_read_tokens = int(
        usage.get("cache_read_input_tokens", details.get("cache_read", 0)) or 0
    )
    cache_creation_tokens = int(
        usage.get("cache_creation_input_tokens", details.get("cache_creation", 0)) or 0
    )
    hit_tokens = int(usage.get("prompt_cache_hit_tokens", cache_read_tokens) or 0)
    miss_tokens = int(
        usage.get(
            "prompt_cache_miss_tokens",
            max(0, input_tokens - hit_tokens),
        )
        or 0
    )
    return TurnUsage(
        turn=0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        prompt_cache_hit_tokens=hit_tokens,
        prompt_cache_miss_tokens=miss_tokens,
    )


def _turn_with_number(turn_number: int, usage: TurnUsage) -> TurnUsage:
    return TurnUsage(
        turn=turn_number,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        prompt_cache_hit_tokens=usage.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=usage.prompt_cache_miss_tokens,
    )


def _seed_memory(agent_name: str | None, memory_text: str) -> None:
    storage = get_memory_storage()
    memory_data = create_empty_memory()
    now = utc_now_iso_z()
    memory_data["facts"] = [
        {
            "id": "benchmark-fact",
            "content": memory_text,
            "category": "benchmark",
            "confidence": 1.0,
            "createdAt": now,
            "updatedAt": now,
        }
    ]
    storage.save(memory_data, agent_name)


def _snapshot_memory(agent_name: str | None, *, phase: str) -> MemoryRecord:
    storage = get_memory_storage()
    memory_data = storage.reload(agent_name)
    normalized = copy.deepcopy(memory_data)
    normalized.pop("lastUpdated", None)
    for fact in normalized.get("facts", []):
        if isinstance(fact, dict):
            fact.pop("createdAt", None)
            fact.pop("updatedAt", None)
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    facts = memory_data.get("facts", [])
    first_fact = facts[0] if facts and isinstance(facts[0], dict) else {}
    preview = str(first_fact.get("content", "")).splitlines()[0][:120]
    return MemoryRecord(
        phase=phase,
        sha256=hashlib.sha256(encoded).hexdigest()[:16],
        memory_bytes=len(encoded),
        fact_count=len(facts),
        first_fact_preview=preview,
    )


def _prompt_snapshot(shared: SharedRuntime) -> str:
    with _patched_prompt_clock():
        return build_session_prompt(
            subagent_enabled=shared.subagent_enabled,
            max_concurrent_subagents=shared.max_concurrent_subagents,
            agent_name=shared.agent_name,
            available_skills=(
                set(shared.agent_config.skills)
                if shared.agent_config and shared.agent_config.skills is not None
                else None
            ),
        )


def _build_shared_runtime(agent_name: str | None, model_name: str) -> SharedRuntime:
    app_config = reload_app_config()
    agent_config = load_agent_config(agent_name) if agent_name else None
    model_config = app_config.get_model_config(model_name)
    if model_config is None:
        raise ValueError(f"Model config {model_name!r} not found")

    model_overrides: dict[str, Any] = {}
    if agent_config:
        if agent_config.provider_model:
            model_overrides["model"] = agent_config.provider_model
        if agent_config.api_key:
            model_overrides["api_key"] = agent_config.api_key
        if agent_config.base_url:
            model_overrides["base_url"] = agent_config.base_url

    tools = get_available_tools(
        model_name=model_name,
        groups=agent_config.tool_groups if agent_config else None,
        subagent_enabled=True,
    )
    return SharedRuntime(
        agent_name=agent_name,
        model_name=model_name,
        agent_config=agent_config,
        model_config=model_config,
        model_overrides=model_overrides,
        tools=tools,
        subagent_enabled=True,
        max_concurrent_subagents=3,
    )


def _build_graph(
    *,
    shared: SharedRuntime,
    use_prompt_cache: bool,
    checkpointer: InMemorySaver,
) -> Any:
    prompt_cache_middleware = None
    if use_prompt_cache:
        prompt_cache_middleware = _make_prompt_cache_middleware(
            model_name=shared.model_name,
            model_config=shared.model_config,
            agent_name=shared.agent_name,
            agent_config=shared.agent_config,
            subagent_enabled=shared.subagent_enabled,
            max_concurrent_subagents=shared.max_concurrent_subagents,
            is_bootstrap=False,
        )

    middlewares = _build_middlewares(
        {
            "configurable": {
                "is_plan_mode": True,
                "subagent_enabled": shared.subagent_enabled,
                "max_concurrent_subagents": shared.max_concurrent_subagents,
                "title_enabled": False,
            }
        },
        model_name=shared.model_name,
        agent_name=shared.agent_name,
        prompt_cache_middleware=prompt_cache_middleware,
    )
    middlewares = [mw for mw in middlewares if not isinstance(mw, MemoryMiddleware)]

    system_prompt = (
        PROMPT_CACHE_PLACEHOLDER if use_prompt_cache else _prompt_snapshot(shared)
    )
    model = create_chat_model(
        name=shared.model_name,
        thinking_enabled=False,
        **shared.model_overrides,
    )
    return create_agent(
        model=model,
        tools=shared.tools,
        middleware=middlewares,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        state_schema=ThreadState,
    )


async def _invoke_turn(
    *,
    graph: Any,
    thread_id: str,
    agent_name: str | None,
    model_name: str,
    prompt: str,
) -> TurnUsage:
    runtime = Runtime(context={"thread_id": thread_id, "agent_name": agent_name})
    config = _build_runtime_config(agent_name, thread_id, model_name)
    config["configurable"]["__pregel_runtime"] = runtime
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=prompt)]},
        config=config,
        context={"thread_id": thread_id, "agent_name": agent_name},
    )
    last_message = result["messages"][-1]
    return _extract_usage(last_message)


async def _run_mode(
    *,
    mode: str,
    use_prompt_cache: bool,
    shared: SharedRuntime,
    seed_prompt: str,
    measure_prompt: str,
) -> RunResult:
    thread_id = f"{mode}-{uuid.uuid4().hex[:10]}"
    checkpointer = InMemorySaver()

    _seed_memory(shared.agent_name, _MEMORY_TEXT_A)
    initial_memory = _snapshot_memory(shared.agent_name, phase="before_turn_1")
    initial_prompt_text = _prompt_snapshot(shared)
    initial_sha, initial_bytes = _hash_prompt(initial_prompt_text)

    # Agent instance 1
    graph_turn1 = _build_graph(
        shared=shared,
        use_prompt_cache=use_prompt_cache,
        checkpointer=checkpointer,
    )
    turn1 = _turn_with_number(
        1,
        await _invoke_turn(
            graph=graph_turn1,
            thread_id=thread_id,
            agent_name=shared.agent_name,
            model_name=shared.model_name,
            prompt=seed_prompt,
        ),
    )

    _seed_memory(shared.agent_name, _MEMORY_TEXT_B)
    mutated_memory = _snapshot_memory(
        shared.agent_name,
        phase="before_turn_2_after_manual_memory_mutation",
    )
    restart_prompt_text = (
        initial_prompt_text if use_prompt_cache else _prompt_snapshot(shared)
    )
    restart_sha, restart_bytes = _hash_prompt(restart_prompt_text)

    # Agent instance 2 (simulated restart with the same checkpoint)
    graph_turn2 = _build_graph(
        shared=shared,
        use_prompt_cache=use_prompt_cache,
        checkpointer=checkpointer,
    )
    turn2 = _turn_with_number(
        2,
        await _invoke_turn(
            graph=graph_turn2,
            thread_id=thread_id,
            agent_name=shared.agent_name,
            model_name=shared.model_name,
            prompt=measure_prompt,
        ),
    )

    return RunResult(
        mode=mode,
        thread_id=thread_id,
        agent_name=shared.agent_name,
        model_name=shared.model_name,
        initial_memory=initial_memory,
        mutated_memory=mutated_memory,
        initial_prompt=PromptRecord(
            phase="before_turn_1",
            sha256=initial_sha,
            prompt_bytes=initial_bytes,
        ),
        restart_prompt=PromptRecord(
            phase="before_turn_2_after_restart",
            sha256=restart_sha,
            prompt_bytes=restart_bytes,
        ),
        turn1=turn1,
        turn2=turn2,
    )


def _print_run(result: RunResult) -> None:
    print(
        f"[{result.mode}] agent={result.agent_name or 'default'} model={result.model_name} "
        f"thread_id={result.thread_id}"
    )
    print(
        f"  memory@turn1 sha={result.initial_memory.sha256} bytes={result.initial_memory.memory_bytes} "
        f"facts={result.initial_memory.fact_count}"
    )
    print(
        f"  memory@turn2 sha={result.mutated_memory.sha256} bytes={result.mutated_memory.memory_bytes} "
        f"facts={result.mutated_memory.fact_count}"
    )
    print(f"  memory_preview@turn1={result.initial_memory.first_fact_preview}")
    print(f"  memory_preview@turn2={result.mutated_memory.first_fact_preview}")
    print(
        f"  prompt@turn1 sha={result.initial_prompt.sha256} bytes={result.initial_prompt.prompt_bytes}"
    )
    print(
        f"  prompt@turn2 sha={result.restart_prompt.sha256} bytes={result.restart_prompt.prompt_bytes}"
    )
    for turn in (result.turn1, result.turn2):
        print(
            f"  turn={turn.turn} input={turn.input_tokens} output={turn.output_tokens} "
            f"total={turn.total_tokens} cache_read={turn.cache_read_tokens} "
            f"cache_create={turn.cache_creation_tokens} hit={turn.prompt_cache_hit_tokens} "
            f"miss={turn.prompt_cache_miss_tokens} billable_input={turn.billable_input_tokens}"
        )


def _print_comparison(baseline: RunResult, cached: RunResult) -> None:
    same_initial_memory = (
        baseline.initial_memory.sha256 == cached.initial_memory.sha256
        and baseline.initial_memory.memory_bytes == cached.initial_memory.memory_bytes
    )
    same_mutated_memory = (
        baseline.mutated_memory.sha256 == cached.mutated_memory.sha256
        and baseline.mutated_memory.memory_bytes == cached.mutated_memory.memory_bytes
    )
    manual_memory_mutation_confirmed = (
        baseline.initial_memory.sha256 != baseline.mutated_memory.sha256
        and cached.initial_memory.sha256 != cached.mutated_memory.sha256
    )
    same_first_prompt = (
        baseline.initial_prompt.sha256 == cached.initial_prompt.sha256
        and baseline.initial_prompt.prompt_bytes == cached.initial_prompt.prompt_bytes
    )
    baseline_prompt_changed = baseline.initial_prompt.sha256 != baseline.restart_prompt.sha256
    cached_prompt_reused = cached.initial_prompt.sha256 == cached.restart_prompt.sha256

    base_turn2 = baseline.turn2
    cached_turn2 = cached.turn2
    miss_savings = base_turn2.billable_input_tokens - cached_turn2.billable_input_tokens
    miss_ratio = (
        miss_savings / base_turn2.billable_input_tokens
        if base_turn2.billable_input_tokens
        else 0.0
    )

    print()
    print("Memory checks:")
    print(f"  same_initial_memory_across_arms={same_initial_memory}")
    print(f"  same_mutated_memory_across_arms={same_mutated_memory}")
    print(f"  manual_memory_mutation_confirmed={manual_memory_mutation_confirmed}")
    print()
    print("Prompt checks:")
    print(f"  same_first_prompt_across_arms={same_first_prompt}")
    print(f"  no_cache_prompt_changed_after_memory_mutation={baseline_prompt_changed}")
    print(f"  cache_prompt_reused_after_restart={cached_prompt_reused}")
    print()
    print("Turn 2 comparison:")
    print(f"  baseline_hit={base_turn2.prompt_cache_hit_tokens}")
    print(f"  cached_hit={cached_turn2.prompt_cache_hit_tokens}")
    print(f"  baseline_miss={base_turn2.prompt_cache_miss_tokens}")
    print(f"  cached_miss={cached_turn2.prompt_cache_miss_tokens}")
    print(f"  baseline_billable_input={base_turn2.billable_input_tokens}")
    print(f"  cached_billable_input={cached_turn2.billable_input_tokens}")
    print(f"  billable_input_savings={miss_savings}")
    print(f"  billable_input_savings_ratio={_fmt_pct(miss_ratio)}")

    if (
        base_turn2.prompt_cache_hit_tokens == 0
        and cached_turn2.prompt_cache_hit_tokens == 0
        and base_turn2.cache_read_tokens == 0
        and cached_turn2.cache_read_tokens == 0
    ):
        print("  observation=provider did not expose a positive cache-hit signal in this run")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a controlled A/B benchmark for prompt caching."
    )
    parser.add_argument(
        "--agent-name",
        default=None,
        help="Agent name to benchmark. Defaults to config.active_agent, otherwise global/default agent.",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Model profile name. Defaults to the runtime default resolver.",
    )
    parser.add_argument(
        "--seed-prompt",
        default="我叫白伟琦，请把这个写入你的记忆中",
        help="First-turn prompt used to seed thread state.",
    )
    parser.add_argument(
        "--measure-prompt",
        default="请介绍一下你自己",
        help="Second-turn prompt used for the cache comparison after restart.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write JSON results.",
    )
    args = parser.parse_args()

    app_config = reload_app_config()
    agent_name = args.agent_name if args.agent_name is not None else app_config.active_agent
    model_name = args.model_name or _resolve_model_name()

    if not warm_enabled_skills_cache():
        print("warning: skills cache warm-up timed out; prompt content may be incomplete")

    shared = _build_shared_runtime(agent_name=agent_name, model_name=model_name)

    storage = get_memory_storage()
    memory_backup = copy.deepcopy(storage.load(agent_name))
    try:
        baseline = await _run_mode(
            mode="no-cache",
            use_prompt_cache=False,
            shared=shared,
            seed_prompt=args.seed_prompt,
            measure_prompt=args.measure_prompt,
        )
        cached = await _run_mode(
            mode="cache-on",
            use_prompt_cache=True,
            shared=shared,
            seed_prompt=args.seed_prompt,
            measure_prompt=args.measure_prompt,
        )
    finally:
        storage.save(memory_backup, agent_name)

    _print_run(baseline)
    print()
    _print_run(cached)
    _print_comparison(baseline, cached)

    if args.output_json:
        payload = {
            "baseline": asdict(baseline),
            "cached": asdict(cached),
            "measured_fields": [
                "memory_sha256",
                "memory_bytes",
                "fact_count",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cache_read_tokens",
                "cache_creation_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
                "billable_input_tokens",
            ],
            "note": "This benchmark measures provider-reported prompt/cache usage fields, not raw internal KV pages.",
        }
        Path(args.output_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
