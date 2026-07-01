"""Compare prompt-caching token usage from debug.log.

This script parses prompt cache usage lines and compares a baseline
miss-only run against cached runs so you can verify real token savings.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


LINE_RE = re.compile(
    r"Prompt cache usage: model=(?P<model>[^ ]+) usage=(?P<usage>\{.*\})"
)


@dataclass(frozen=True)
class CacheSample:
    line_no: int
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    hit_tokens: int
    miss_tokens: int

    @property
    def cached_input_ratio(self) -> float:
        if self.input_tokens <= 0:
            return 0.0
        return self.hit_tokens / self.input_tokens

    @property
    def miss_ratio(self) -> float:
        if self.input_tokens <= 0:
            return 0.0
        return self.miss_tokens / self.input_tokens


def _load_samples(log_path: Path) -> list[CacheSample]:
    samples: list[CacheSample] = []
    for line_no, raw_line in enumerate(log_path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        match = LINE_RE.search(raw_line)
        if not match:
            continue
        usage = ast.literal_eval(match.group("usage"))
        samples.append(
            CacheSample(
                line_no=line_no,
                model=match.group("model"),
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
                hit_tokens=int(usage.get("prompt_cache_hit_tokens", 0) or 0),
                miss_tokens=int(usage.get("prompt_cache_miss_tokens", 0) or 0),
            )
        )
    return samples


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def main() -> int:
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("debug.log")
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        return 1

    samples = _load_samples(log_path)
    if len(samples) < 2:
        print("Not enough prompt cache samples found.")
        return 1

    baseline = next((s for s in samples if s.hit_tokens == 0 and s.miss_tokens > 0), samples[0])
    cached = [s for s in samples if s.hit_tokens > 0 and s.miss_tokens >= 0]
    if not cached:
        print("No cached samples with prompt_cache_hit_tokens > 0 were found.")
        return 1

    print(f"Log: {log_path}")
    print()
    print("Baseline sample:")
    print(
        f"  line={baseline.line_no} model={baseline.model} "
        f"input_tokens={baseline.input_tokens} hit={baseline.hit_tokens} miss={baseline.miss_tokens}"
    )
    print()
    print("Cached samples:")
    for sample in cached:
        savings_vs_baseline = baseline.input_tokens - sample.miss_tokens
        savings_ratio = savings_vs_baseline / baseline.input_tokens if baseline.input_tokens else 0.0
        print(
            f"  line={sample.line_no} model={sample.model} "
            f"input_tokens={sample.input_tokens} hit={sample.hit_tokens} miss={sample.miss_tokens} "
            f"miss_ratio={_fmt_pct(sample.miss_ratio)} cached_ratio={_fmt_pct(sample.cached_input_ratio)} "
            f"savings_vs_baseline={savings_vs_baseline} ({_fmt_pct(savings_ratio)})"
        )

    avg_hit = sum(s.hit_tokens for s in cached) / len(cached)
    avg_miss = sum(s.miss_tokens for s in cached) / len(cached)
    avg_input = sum(s.input_tokens for s in cached) / len(cached)
    avg_savings = baseline.input_tokens - avg_miss
    avg_savings_ratio = avg_savings / baseline.input_tokens if baseline.input_tokens else 0.0

    print()
    print("Aggregate:")
    print(f"  cached_runs={len(cached)}")
    print(f"  avg_input_tokens={avg_input:.2f}")
    print(f"  avg_hit_tokens={avg_hit:.2f}")
    print(f"  avg_miss_tokens={avg_miss:.2f}")
    print(f"  avg_savings_vs_baseline={avg_savings:.2f} ({_fmt_pct(avg_savings_ratio)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
