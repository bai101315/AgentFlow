"""Benchmark multi-turn deferred tool promotion token overhead.

This script measures what happens after the agent calls ``tool_search`` and
promotes one or more deferred MCP tools. It does not call an LLM. It uses the
same MCP loading path and the real DeferredToolRegistry/tool_search promotion
logic, then estimates tokens over serialized OpenAI-function tool schemas.

Two token views are reported:

- resident_tool_context_tokens:
  Tool schema/name-list overhead that stays in later model requests after
  promotion. This is the clean comparison against binding all MCP schemas.
- with_tool_search_history_tokens:
  The resident overhead plus accumulated tool_search JSON results. In a normal
  unsummarized multi-turn conversation, those tool results can remain in message
  history, so this is closer to a practical multi-turn upper-bound estimate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

for path in (BACKEND_DIR, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from benchmark_tool_search_tokens import (  # noqa: E402
    _deferred_tools_prompt,
    _load_mcp_tools,
    _load_tiktoken_encoding,
    _percent_saved,
)


def _count_tokens(value: Any, encoding) -> int:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(encoding.encode(text))


def _summary(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"min": 0, "max": 0, "mean": 0, "median": 0, "p90": 0}
    sorted_values = sorted(values)
    p90_index = min(len(sorted_values) - 1, int(len(sorted_values) * 0.9))
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.fmean(values), 2),
        "median": round(statistics.median(values), 2),
        "p90": sorted_values[p90_index],
    }


def _select_batch(
    remaining: list[dict[str, Any]],
    *,
    strategy: str,
    batch_size: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    if batch_size <= 0:
        return []
    if strategy == "largest":
        ordered = sorted(remaining, key=lambda item: int(item["schema_tokens"]), reverse=True)
    elif strategy == "smallest":
        ordered = sorted(remaining, key=lambda item: int(item["schema_tokens"]))
    elif strategy == "mixed":
        ordered_source = sorted(remaining, key=lambda item: int(item["schema_tokens"]), reverse=True)
        ordered = []
        left = 0
        right = len(ordered_source) - 1
        take_large = True
        while left <= right:
            if take_large:
                ordered.append(ordered_source[left])
                left += 1
            else:
                ordered.append(ordered_source[right])
                right -= 1
            take_large = not take_large
    elif strategy == "average":
        mean_tokens = statistics.fmean(int(item["schema_tokens"]) for item in remaining)
        ordered = sorted(
            remaining,
            key=lambda item: (
                abs(int(item["schema_tokens"]) - mean_tokens),
                str(item["name"]).lower(),
            ),
        )
    elif strategy == "random":
        ordered = list(remaining)
        rng.shuffle(ordered)
    else:
        raise ValueError(f"Unsupported strategy: {strategy}")
    return ordered[:batch_size]


def _resident_context_tokens(
    *,
    remaining_names: list[str],
    promoted_schema_tokens: int,
    tool_search_schema_tokens: int,
    encoding,
) -> dict[str, int]:
    names_prompt_tokens = _count_tokens(_deferred_tools_prompt(remaining_names), encoding) if remaining_names else 0
    return {
        "deferred_names_prompt_tokens": names_prompt_tokens,
        "tool_search_schema_tokens": tool_search_schema_tokens,
        "promoted_schema_tokens": promoted_schema_tokens,
        "resident_tool_context_tokens": names_prompt_tokens + tool_search_schema_tokens + promoted_schema_tokens,
    }


def run_multiturn_benchmark(
    *,
    config_path: Path,
    model: str | None,
    turns: int,
    batch_size: int,
    strategy: str,
    seed: int,
    allow_empty: bool,
    npm_cache: Path | None,
) -> dict[str, Any]:
    from langchain_core.utils.function_calling import convert_to_openai_function
    from tools.builtins.tool_search import DeferredToolRegistry, set_deferred_registry, tool_search

    encoding = _load_tiktoken_encoding(model)
    tools = asyncio.run(_load_mcp_tools(config_path, npm_cache))
    if not tools and not allow_empty:
        raise RuntimeError(
            "No MCP tools were loaded. The benchmark would be misleading with an empty tool set. "
            "Check enabled MCP servers, npm/npx availability, credentials, and network/cache access. "
            "Use --allow-empty only for script smoke tests."
        )

    schemas_by_name = {tool.name: convert_to_openai_function(tool) for tool in tools}
    per_tool = [
        {
            "name": tool.name,
            "schema_tokens": _count_tokens(schemas_by_name[tool.name], encoding),
        }
        for tool in tools
    ]
    per_tool.sort(key=lambda item: str(item["name"]).lower())

    baseline_all_mcp_schema_tokens = _count_tokens(list(schemas_by_name.values()), encoding)
    tool_search_schema_tokens = _count_tokens([convert_to_openai_function(tool_search)], encoding)
    rng = random.Random(seed)

    registry = DeferredToolRegistry()
    for tool in tools:
        registry.register(tool)
    set_deferred_registry(registry)

    promoted_names: list[str] = []
    accumulated_tool_search_result_tokens = 0
    states: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    def append_state(label: str) -> dict[str, Any]:
        remaining_names = sorted(registry.deferred_names, key=str.lower)
        promoted_schema_tokens = sum(int(item["schema_tokens"]) for item in per_tool if item["name"] in set(promoted_names))
        resident = _resident_context_tokens(
            remaining_names=remaining_names,
            promoted_schema_tokens=promoted_schema_tokens,
            tool_search_schema_tokens=tool_search_schema_tokens,
            encoding=encoding,
        )
        resident_total = resident["resident_tool_context_tokens"]
        with_history_total = resident_total + accumulated_tool_search_result_tokens
        state = {
            "label": label,
            "promoted_count": len(promoted_names),
            "remaining_deferred_count": len(remaining_names),
            **resident,
            "accumulated_tool_search_result_tokens": accumulated_tool_search_result_tokens,
            "with_tool_search_history_tokens": with_history_total,
            "resident_saved_tokens_vs_all_mcp": baseline_all_mcp_schema_tokens - resident_total,
            "resident_saved_percent_vs_all_mcp": _percent_saved(baseline_all_mcp_schema_tokens, resident_total),
            "with_history_saved_tokens_vs_all_mcp": baseline_all_mcp_schema_tokens - with_history_total,
            "with_history_saved_percent_vs_all_mcp": _percent_saved(baseline_all_mcp_schema_tokens, with_history_total),
        }
        states.append(state)
        return state

    append_state("turn_0_before_any_tool_search")

    for turn_index in range(1, turns + 1):
        remaining = [item for item in per_tool if item["name"] in registry.deferred_names]
        if not remaining:
            break

        batch = _select_batch(remaining, strategy=strategy, batch_size=batch_size, rng=rng)
        selected_names = [str(item["name"]) for item in batch]
        query = "select:" + ",".join(selected_names)

        before_resident = states[-1]["resident_tool_context_tokens"]
        result = tool_search.invoke({"query": query})
        result_tokens = _count_tokens(result, encoding)
        accumulated_tool_search_result_tokens += result_tokens
        promoted_names.extend(name for name in selected_names if name not in promoted_names)
        after_state = append_state(f"turn_{turn_index}_after_promoting_{len(selected_names)}_tool(s)")

        promoted_schema_tokens = sum(int(item["schema_tokens"]) for item in batch)
        events.append(
            {
                "turn": turn_index,
                "query": query,
                "promoted_tools": batch,
                "promoted_schema_tokens_this_turn": promoted_schema_tokens,
                "tool_search_result_tokens_this_turn": result_tokens,
                "resident_tokens_before_turn": before_resident,
                "resident_tokens_after_turn": after_state["resident_tool_context_tokens"],
                "resident_delta_tokens": after_state["resident_tool_context_tokens"] - before_resident,
                "resident_saved_percent_after_turn": after_state["resident_saved_percent_vs_all_mcp"],
                "with_history_saved_percent_after_turn": after_state["with_history_saved_percent_vs_all_mcp"],
            }
        )

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(PROJECT_ROOT),
            "config_path": str(config_path.resolve()),
            "encoding": getattr(encoding, "name", "unknown"),
            "model_for_encoding": model,
            "mcp_tools_loaded": len(tools),
            "turns_requested": turns,
            "batch_size": batch_size,
            "promotion_strategy": strategy,
            "seed": seed if strategy == "random" else None,
            "assumptions": [
                "No LLM calls are made; token counts are tokenizer estimates over serialized tool definitions and tool_search JSON results.",
                "Baseline means all MCP tool schemas are bound to the model on every request.",
                "Resident context means deferred tool name list + tool_search schema + schemas for tools promoted so far.",
                "Promoted tools reduce remaining savings because their full schemas become active in later model calls.",
                "with_tool_search_history_tokens adds accumulated tool_search results as a practical multi-turn upper-bound when history is unsummarized.",
                "Configured built-in tools and non-tool prompt sections are excluded because they are shared by both variants.",
            ],
        },
        "baseline": {
            "all_mcp_schema_tokens": baseline_all_mcp_schema_tokens,
            "tool_schema_token_distribution": _summary([int(item["schema_tokens"]) for item in per_tool]),
        },
        "states": states,
        "events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estimate deferred tool_search savings across multiple promotion turns.",
    )
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    parser.add_argument("--model", default=None)
    parser.add_argument("--turns", type=int, default=4, help="Number of tool_search promotion turns to simulate.")
    parser.add_argument("--batch-size", type=int, default=3, help="Number of tools to promote per turn.")
    parser.add_argument(
        "--strategy",
        choices=("largest", "smallest", "mixed", "average", "random"),
        default="largest",
        help="Promotion order. largest is a conservative worst-case for token growth.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed used only with --strategy random.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--npm-cache",
        type=Path,
        default=PROJECT_ROOT / ".cache" / "npm",
        help="NPM cache directory for stdio MCP servers launched through npx.",
    )
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()

    if args.turns < 0:
        raise SystemExit("--turns must be >= 0")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")

    if args.npm_cache:
        args.npm_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("NPM_CONFIG_CACHE", str(args.npm_cache.resolve()))
        os.environ.setdefault("npm_config_cache", str(args.npm_cache.resolve()))

    result = run_multiturn_benchmark(
        config_path=args.config,
        model=args.model,
        turns=args.turns,
        batch_size=args.batch_size,
        strategy=args.strategy,
        seed=args.seed,
        allow_empty=args.allow_empty,
        npm_cache=args.npm_cache,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
