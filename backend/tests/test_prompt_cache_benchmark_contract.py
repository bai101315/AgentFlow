"""Contract checks for the prompt-cache benchmark harness.

This file does not assert live token savings by itself. Its role is to lock in
the benchmark design so the A/B script keeps comparing one variable only:
whether the second agent instance reuses the cached frozen system prompt.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_benchmark_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_prompt_cache_ab.py"
    spec = importlib.util.spec_from_file_location("benchmark_prompt_cache_ab", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_uses_frozen_prompt_builder_for_both_arms():
    mod = _load_benchmark_module()

    assert hasattr(mod, "_prompt_snapshot")
    assert hasattr(mod, "_build_graph")


def test_benchmark_declares_provider_usage_limitation():
    mod = _load_benchmark_module()

    doc = mod.__doc__ or ""
    assert "provider-reported cache usage" in doc
    assert "does not claim access to the provider's raw internal KV pages" in doc


def test_billable_input_prefers_prompt_cache_miss_tokens():
    mod = _load_benchmark_module()

    usage = mod.TurnUsage(
        turn=2,
        input_tokens=1000,
        output_tokens=10,
        total_tokens=1010,
        cache_read_tokens=800,
        cache_creation_tokens=0,
        prompt_cache_hit_tokens=800,
        prompt_cache_miss_tokens=200,
    )

    assert usage.billable_input_tokens == 200


def test_billable_input_falls_back_to_input_minus_hits():
    mod = _load_benchmark_module()

    usage = mod.TurnUsage(
        turn=2,
        input_tokens=1000,
        output_tokens=10,
        total_tokens=1010,
        cache_read_tokens=800,
        cache_creation_tokens=0,
        prompt_cache_hit_tokens=800,
        prompt_cache_miss_tokens=0,
    )

    assert usage.billable_input_tokens == 200
