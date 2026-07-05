from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .store import ObservabilityStore


def build_thread_summary(store: ObservabilityStore, thread_id: str) -> dict[str, Any]:
    thread_totals = store.get_thread_totals(thread_id) or {
        "totals": {},
        "metadata": {},
        "updated_at": None,
    }
    traces = store.list_traces_for_thread(thread_id)
    legacy_imports = store.list_legacy_imports_for_thread(thread_id)
    merged_legacy_files = [Path(item["source_path"]).name for item in legacy_imports]
    merged_legacy_session_ids: list[str] = []
    for item in legacy_imports:
        payload = item["payload"]
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id and session_id not in merged_legacy_session_ids:
            merged_legacy_session_ids.append(session_id)

    turns: list[dict[str, Any]] = []
    for item in legacy_imports:
        payload = item["payload"]
        legacy_turns = payload.get("turns") if isinstance(payload.get("turns"), list) else []
        for turn in legacy_turns:
            if isinstance(turn, dict):
                turns.append(dict(turn))

    recent_traces: list[dict[str, Any]] = []
    live_index = len(turns)
    for trace in traces:
        live_index += 1
        tool_calls = store.list_tool_calls_for_trace(trace["trace_id"])
        tool_names = [call.get("tool_name") for call in tool_calls if call.get("tool_name")]
        summary = trace.get("summary", {})
        diagnostics = summary.get("diagnostics") or {}
        trace_row = {
            "turn_index": live_index,
            "trace_id": trace.get("trace_id"),
            "timestamp": trace["started_at"],
            "elapsed_ms": trace.get("elapsed_ms"),
            "completed": trace.get("completed", False),
            "failure_reason": trace.get("failure_reason"),
            "content_mode": trace.get("content_mode"),
            "tool_names": tool_names,
            "tool_call_count": len(tool_calls),
            "had_any_failure": summary.get("had_any_failure", False),
            "failed_tool_call_count": summary.get("failed_tool_call_count", 0),
            "failed_tool_names": summary.get("failed_tool_names", []),
            "recovered_after_failure": summary.get("recovered_after_failure", False),
            "failure_events": summary.get("failure_events", []),
            "input_tokens": trace.get("usage", {}).get("input_tokens", 0),
            "output_tokens": trace.get("usage", {}).get("output_tokens", 0),
            "total_tokens": trace.get("usage", {}).get("total_tokens", 0),
            "billable_input_tokens": trace.get("usage", {}).get("billable_input_tokens", 0),
            "prompt_cache_hit_rate": trace.get("usage", {}).get("prompt_cache_hit_rate"),
            "trace_file_path": summary.get("trace_file_path"),
            "trace_file_reason": summary.get("trace_file_reason"),
            "diagnostic_reasons": diagnostics.get("reasons", []),
            "user_input": trace.get("user_input_preview"),
            "assistant_output": trace.get("assistant_output_preview"),
        }
        turns.append(
            {
                **trace_row,
                "thread_id": thread_id,
                "usage": trace.get("usage", {}),
            }
        )
        recent_traces.append(trace_row)

    metadata = thread_totals["metadata"]
    initial_agent_name = metadata.get("initial_agent_name")
    initial_model_name = metadata.get("initial_model_name")
    started_at = metadata.get("started_at")
    ended_at = thread_totals["totals"].get("ended_at")
    exit_reason = thread_totals["totals"].get("exit_reason")

    return {
        "session_id": thread_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_reason": exit_reason,
        "initial_agent_name": initial_agent_name,
        "initial_thread_id": thread_id,
        "initial_model_name": initial_model_name,
        "merged_legacy_session_ids": merged_legacy_session_ids,
        "merged_legacy_files": merged_legacy_files,
        "latest_trace": recent_traces[-1] if recent_traces else None,
        "recent_traces": recent_traces[-20:],
        "turns": turns,
        "summary": thread_totals["totals"],
    }


def write_thread_views(store: ObservabilityStore, thread_id: str) -> dict[str, Any]:
    payload = build_thread_summary(store, thread_id)
    thread_path = store.threads_dir / f"{thread_id}.json"
    _atomic_write_json(thread_path, payload)

    session_log_path = store.root / "session_logs" / f"{thread_id}.json"
    session_log_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(session_log_path, payload)
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile(mode="w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8")
    try:
        with temp:
            json.dump(payload, temp, ensure_ascii=False, indent=2)
        Path(temp.name).replace(path)
    finally:
        try:
            Path(temp.name).unlink(missing_ok=True)
        except OSError:
            pass
