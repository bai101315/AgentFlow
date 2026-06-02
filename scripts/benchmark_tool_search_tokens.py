"""Benchmark prompt-token overhead saved by deferred MCP tool schemas.

This script is intentionally offline with respect to LLM calls: it does not
send prompts to a model. It loads the project's enabled MCP tools, serializes
their LangChain tool schemas to OpenAI function format, and estimates token
counts with tiktoken.

The measurement isolates MCP tool schema overhead only. Other prompt sections
and non-MCP tools are intentionally excluded because they are present in both
the baseline and deferred-tool-search paths.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load_tiktoken_encoding(model: str | None):
    import tiktoken

    if model:
        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            pass
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(value: Any, encoding) -> int:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(encoding.encode(text))


def _percent_saved(baseline: int, optimized: int) -> float:
    if baseline <= 0:
        return 0.0
    return round((baseline - optimized) / baseline * 100, 2)


def _summary(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {
            "min": 0,
            "max": 0,
            "mean": 0,
            "median": 0,
            "p90": 0,
        }
    sorted_values = sorted(values)
    p90_index = min(len(sorted_values) - 1, int(len(sorted_values) * 0.9))
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.fmean(values), 2),
        "median": round(statistics.median(values), 2),
        "p90": sorted_values[p90_index],
    }


def _prepare_extensions_config_for_benchmark(npm_cache: Path | None) -> Path | None:
    if npm_cache is None:
        return None

    from config.extensions_config import ExtensionsConfig

    original_path = ExtensionsConfig.resolve_config_path()
    if original_path is None:
        return None

    raw_config = json.loads(original_path.read_text(encoding="utf-8"))
    cache_value = str(npm_cache.resolve())
    for server_config in raw_config.get("mcpServers", {}).values():
        if not server_config.get("enabled", True):
            continue
        if server_config.get("type", "stdio") != "stdio":
            continue
        server_env = server_config.get("env") or {}
        server_config["env"] = {
            **os.environ,
            **server_env,
            "NPM_CONFIG_CACHE": cache_value,
            "npm_config_cache": cache_value,
        }

    cache_dir = PROJECT_ROOT / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".extensions_config.json",
        prefix="benchmark_",
        dir=cache_dir,
        delete=False,
    ) as temp_file:
        json.dump(raw_config, temp_file, ensure_ascii=False, indent=2)
        temp_file.write("\n")
        return Path(temp_file.name)


async def _load_mcp_tools(config_path: Path, npm_cache: Path | None):
    from config.app_config import reload_app_config
    from deer_flow_mcp.cache import initialize_mcp_tools, reset_mcp_tools_cache

    temp_extensions_path = _prepare_extensions_config_for_benchmark(npm_cache)
    previous_extensions_path = os.environ.get("DEER_FLOW_EXTENSIONS_CONFIG_PATH")
    try:
        if temp_extensions_path is not None:
            os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(temp_extensions_path)
        reload_app_config(str(config_path))
        reset_mcp_tools_cache()
        return await initialize_mcp_tools()
    finally:
        if previous_extensions_path is None:
            os.environ.pop("DEER_FLOW_EXTENSIONS_CONFIG_PATH", None)
        else:
            os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = previous_extensions_path
        if temp_extensions_path is not None:
            temp_extensions_path.unlink(missing_ok=True)


def _deferred_tools_prompt(tool_names: list[str]) -> str:
    names = "\n".join(tool_names)
    return f"<available-deferred-tools>\n{names}\n</available-deferred-tools>"


def run_benchmark(config_path: Path, model: str | None, top_n: int, allow_empty: bool, npm_cache: Path | None) -> dict[str, Any]:
    from langchain_core.utils.function_calling import convert_to_openai_function
    from tools.builtins.tool_search import tool_search

    encoding = _load_tiktoken_encoding(model)
    tools = asyncio.run(_load_mcp_tools(config_path, npm_cache))
    if not tools and not allow_empty:
        raise RuntimeError(
            "No MCP tools were loaded. The benchmark would be misleading with an empty tool set. "
            "Check enabled MCP servers, npm/npx availability, credentials, and network/cache access. "
            "Use --allow-empty only for script smoke tests."
        )

    tool_schemas = [convert_to_openai_function(tool) for tool in tools]
    tool_names = [tool.name for tool in tools]

    baseline_all_mcp_schema_tokens = _count_tokens(tool_schemas, encoding)
    deferred_names_prompt_tokens = _count_tokens(_deferred_tools_prompt(tool_names), encoding)
    tool_search_schema_tokens = _count_tokens([convert_to_openai_function(tool_search)], encoding)
    deferred_initial_tokens = deferred_names_prompt_tokens + tool_search_schema_tokens

    per_tool = [
        {
            "name": name,
            "schema_tokens": _count_tokens(schema, encoding),
        }
        for name, schema in zip(tool_names, tool_schemas, strict=True)
    ]
    per_tool.sort(key=lambda item: item["schema_tokens"], reverse=True)

    selected_for_first_search = per_tool[:top_n]
    first_search_schema_tokens = sum(int(item["schema_tokens"]) for item in selected_for_first_search)
    deferred_worst_case_first_search_tokens = deferred_initial_tokens + first_search_schema_tokens

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(PROJECT_ROOT),
            "config_path": str(config_path.resolve()),
            "encoding": getattr(encoding, "name", "unknown"),
            "model_for_encoding": model,
            "mcp_tools_loaded": len(tools),
            "top_n_first_search": top_n,
            "assumptions": [
                "Only MCP tool schema overhead is measured.",
                "Configured built-in tools and the rest of the system prompt are excluded because they are shared by both variants.",
                "Baseline means all MCP tool schemas are bound to the model on the initial request.",
                "Deferred means the initial request includes the available-deferred-tools name list plus the tool_search schema.",
                "Token counts are tokenizer estimates over serialized tool definitions, not provider billing records.",
            ],
        },
        "token_estimates": {
            "baseline_all_mcp_schemas": baseline_all_mcp_schema_tokens,
            "deferred_names_prompt": deferred_names_prompt_tokens,
            "tool_search_schema": tool_search_schema_tokens,
            "deferred_initial_total": deferred_initial_tokens,
            "initial_saved_tokens": baseline_all_mcp_schema_tokens - deferred_initial_tokens,
            "initial_saved_percent": _percent_saved(
                baseline_all_mcp_schema_tokens,
                deferred_initial_tokens,
            ),
            "deferred_first_search_top_n_total": deferred_worst_case_first_search_tokens,
            "first_search_top_n_saved_tokens": baseline_all_mcp_schema_tokens - deferred_worst_case_first_search_tokens,
            "first_search_top_n_saved_percent": _percent_saved(
                baseline_all_mcp_schema_tokens,
                deferred_worst_case_first_search_tokens,
            ),
        },
        "schema_token_distribution": _summary([int(item["schema_tokens"]) for item in per_tool]),
        "largest_tool_schemas": selected_for_first_search,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estimate prompt-token savings from deferred MCP tool schema exposure.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "config.yaml",
        help="Path to config.yaml.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model name used only to select a tiktoken encoding.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="How many largest schemas to model for a worst-case first tool_search result.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the JSON result.",
    )
    parser.add_argument(
        "--npm-cache",
        type=Path,
        default=PROJECT_ROOT / ".cache" / "npm",
        help="NPM cache directory for stdio MCP servers launched through npx.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow an empty MCP tool set. Intended only for smoke tests.",
    )
    args = parser.parse_args()

    if args.top_n < 1:
        raise SystemExit("--top-n must be >= 1")

    if args.npm_cache:
        args.npm_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("NPM_CONFIG_CACHE", str(args.npm_cache.resolve()))
        os.environ.setdefault("npm_config_cache", str(args.npm_cache.resolve()))

    result = run_benchmark(args.config, args.model, args.top_n, args.allow_empty, args.npm_cache)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
