from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import get_app_config
from config.observability_config import ObservabilityConfig, ObservabilityContentMode

from .redaction import sanitize_preview
from .store import ObservabilityStore
from .summary_view import write_thread_views
from .trajectory import append_jsonl

logger = logging.getLogger(__name__)


_CURRENT_TRACE: ContextVar[TraceContext | None] = ContextVar("agentflow_current_trace", default=None)
_RECORDER: ObservabilityRecorder | None = None


@dataclass
class TraceContext:
    trace_id: str
    thread_id: str
    agent_name: str | None
    model_name: str | None
    started_at: str
    started_perf: float
    content_mode: ObservabilityContentMode
    user_input_preview: str | None
    user_input_hash: str | None
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    cache_events: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False
    failure_reason: str | None = None
    trace_file_written: bool = False
    trace_file_reason: str | None = None


def get_current_trace() -> TraceContext | None:
    return _CURRENT_TRACE.get()


def get_observability_recorder() -> ObservabilityRecorder:
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = ObservabilityRecorder()
    return _RECORDER


class ObservabilityRecorder:
    """Canonical local observability recorder."""

    def __init__(
        self,
        config: ObservabilityConfig | None = None,
        store: ObservabilityStore | None = None,
        *,
        bootstrap_legacy: bool = True,
    ) -> None:
        self._config = config or _load_config()
        self._store = store or ObservabilityStore()
        if bootstrap_legacy:
            self._bootstrap_legacy_session_logs()

    @property
    def config(self) -> ObservabilityConfig:
        return self._config

    @property
    def store(self) -> ObservabilityStore:
        return self._store

    def start_trace(
        self,
        *,
        thread_id: str,
        agent_name: str | None,
        model_name: str | None,
        user_input: str,
        content_mode: ObservabilityContentMode | None = None,
    ) -> tuple[TraceContext | None, Token | None]:
        if not self._config.enabled:
            return None, None

        mode = content_mode or self._config.content_mode
        user_preview = None
        user_hash = None
        if mode != "off":
            preview = sanitize_preview(user_input, max_chars=self._config.max_preview_chars)
            user_preview = preview["preview"]
            user_hash = preview["hash"]

        context = TraceContext(
            trace_id=str(uuid.uuid4()),
            thread_id=thread_id,
            agent_name=agent_name,
            model_name=model_name,
            started_at=_utc_now_iso(),
            started_perf=time.perf_counter(),
            content_mode=mode,
            user_input_preview=user_preview,
            user_input_hash=user_hash,
            usage=_empty_usage(),
        )
        token = _CURRENT_TRACE.set(context)
        return context, token

    def end_trace(
        self,
        context: TraceContext | None,
        token: Token | None,
        *,
        assistant_output: Any,
        completed: bool,
        failure_reason: str | None = None,
        exit_reason: str | None = None,
    ) -> dict[str, Any] | None:
        if context is None:
            return None

        ended_at = _utc_now_iso()
        elapsed_ms = int((time.perf_counter() - context.started_perf) * 1000)
        context.completed = completed
        context.failure_reason = failure_reason

        assistant_preview = None
        assistant_hash = None
        if context.content_mode != "off":
            preview = sanitize_preview(assistant_output, max_chars=self._config.max_preview_chars)
            assistant_preview = preview["preview"]
            assistant_hash = preview["hash"]

        diagnostics = self._build_diagnostics(context=context, elapsed_ms=elapsed_ms)
        failure_summary = self._build_failure_summary(context=context, completed=completed)
        summary = {
            "trace_id": context.trace_id,
            "thread_id": context.thread_id,
            "started_at": context.started_at,
            "ended_at": ended_at,
            "elapsed_ms": elapsed_ms,
            "completed": completed,
            "failure_reason": failure_reason,
            "content_mode": context.content_mode,
            "usage": context.usage,
            "tool_call_count": len(context.tool_calls),
            "failed_tool_call_count": failure_summary["failed_tool_call_count"],
            "had_any_failure": failure_summary["had_any_failure"],
            "failed_tool_names": failure_summary["failed_tool_names"],
            "recovered_after_failure": failure_summary["recovered_after_failure"],
            "failure_events": failure_summary["failure_events"],
            "diagnostics": diagnostics,
        }
        trace_payload = {
            "trace_id": context.trace_id,
            "thread_id": context.thread_id,
            "agent_name": context.agent_name,
            "model_name": context.model_name,
            "started_at": context.started_at,
            "ended_at": ended_at,
            "elapsed_ms": elapsed_ms,
            "content_mode": context.content_mode,
            "completed": completed,
            "failure_reason": failure_reason,
            "user_input_preview": context.user_input_preview,
            "user_input_hash": context.user_input_hash,
            "assistant_output_preview": assistant_preview,
            "assistant_output_hash": assistant_hash,
            "usage": context.usage,
            "summary": summary,
            "failure_summary": failure_summary,
            "diagnostics": diagnostics,
            "model_calls": context.model_calls,
            "tool_calls": context.tool_calls,
            "cache_events": context.cache_events,
        }

        self._store.upsert_trace(trace_payload)
        trace_file_reason = self._trace_file_reason(context=context, diagnostics=diagnostics, completed=completed)
        if trace_file_reason is not None:
            path = self._write_trace_file(trace_payload)
            context.trace_file_written = True
            context.trace_file_reason = trace_file_reason
            trace_payload["trace_file_path"] = str(path)
            trace_payload["trace_file_reason"] = trace_file_reason
            summary["trace_file_path"] = str(path)
            summary["trace_file_reason"] = trace_file_reason
            self._store.upsert_trace(trace_payload)
        self._update_thread_totals(
            context=context,
            assistant_output_preview=assistant_preview,
            ended_at=ended_at,
            exit_reason=exit_reason,
            diagnostics=diagnostics,
        )
        if self._config.write_session_summary_view:
            write_thread_views(self._store, context.thread_id)
        if self._config.export_trajectories:
            self._export_trajectory(trace_payload)

        if token is not None:
            _CURRENT_TRACE.reset(token)
        return trace_payload

    def record_model_call(
        self,
        *,
        trace_id: str,
        model_name: str | None,
        usage: dict[str, Any],
        preview_text: str | None,
        started_at: str,
        ended_at: str,
        elapsed_ms: int,
    ) -> None:
        context = get_current_trace()
        if context is None or context.trace_id != trace_id:
            return

        span_id = str(uuid.uuid4())
        if context.content_mode != "off":
            preview = sanitize_preview(preview_text or "", max_chars=self._config.max_preview_chars)
            payload_preview = preview
        else:
            payload_preview = {}
        payload = {
            "span_id": span_id,
            "trace_id": trace_id,
            "model_name": model_name,
            "usage": usage,
            "preview": payload_preview,
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_ms": elapsed_ms,
        }
        self._store.insert_span(
            {
                "span_id": span_id,
                "trace_id": trace_id,
                "span_type": "model_call",
                "name": model_name or "default",
                "started_at": started_at,
                "ended_at": ended_at,
                "elapsed_ms": elapsed_ms,
                "status": "ok",
                "error_type": None,
                "payload": payload,
            }
        )
        self._store.insert_model_call(payload)
        context.model_calls.append(payload)
        self._merge_usage(context.usage, usage)
        self.record_cache_event(
            trace_id=trace_id,
            cache_name="prompt_cache",
            hit_tokens=int(usage.get("prompt_cache_hit_tokens", 0) or 0),
            miss_tokens=int(usage.get("prompt_cache_miss_tokens", 0) or 0),
            metadata={"model_name": model_name, "usage": usage},
        )

    def record_tool_call(
        self,
        *,
        trace_id: str,
        tool_call_id: str | None,
        tool_name: str,
        args_value: Any,
        result_value: Any,
        status: str,
        error_type: str | None,
        started_at: str,
        ended_at: str,
        elapsed_ms: int,
    ) -> None:
        context = get_current_trace()
        if context is None or context.trace_id != trace_id:
            return

        span_id = str(uuid.uuid4())
        capture_args = self._config.capture_tool_args and context.content_mode != "off"
        capture_results = self._config.capture_tool_results and context.content_mode != "off"
        if capture_args:
            args_preview = sanitize_preview(args_value, max_chars=self._config.max_preview_chars)
            args_preview_payload = args_preview["preview"]
            args_hash = args_preview["hash"]
        else:
            args_preview_payload = None
            args_hash = None
        if capture_results:
            result_preview = sanitize_preview(result_value, max_chars=self._config.max_preview_chars)
            result_preview_payload = result_preview["preview"]
            result_hash = result_preview["hash"]
        else:
            result_preview_payload = None
            result_hash = None
        payload = {
            "span_id": span_id,
            "trace_id": trace_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": status,
            "error_type": error_type,
            "elapsed_ms": elapsed_ms,
            "args_preview": args_preview_payload,
            "args_hash": args_hash,
            "result_preview": result_preview_payload,
            "result_hash": result_hash,
        }
        self._store.insert_span(
            {
                "span_id": span_id,
                "trace_id": trace_id,
                "span_type": "tool_call",
                "name": tool_name,
                "started_at": started_at,
                "ended_at": ended_at,
                "elapsed_ms": elapsed_ms,
                "status": status,
                "error_type": error_type,
                "payload": payload,
            }
        )
        self._store.insert_tool_call(payload)
        context.tool_calls.append(payload)

    def record_cache_event(
        self,
        *,
        trace_id: str,
        cache_name: str,
        hit_tokens: int,
        miss_tokens: int,
        metadata: dict[str, Any],
    ) -> None:
        if hit_tokens <= 0 and miss_tokens <= 0:
            return
        context = get_current_trace()
        if context is None or context.trace_id != trace_id:
            return
        payload = {
            "event_id": str(uuid.uuid4()),
            "trace_id": trace_id,
            "cache_name": cache_name,
            "hit_tokens": hit_tokens,
            "miss_tokens": miss_tokens,
            "metadata": metadata,
            "created_at": _utc_now_iso(),
        }
        self._store.insert_cache_event(payload)
        context.cache_events.append(payload)

    def get_thread_summary(self, thread_id: str) -> dict[str, Any]:
        return write_thread_views(self._store, thread_id)

    def finalize_thread(self, thread_id: str, exit_reason: str) -> None:
        existing = self._store.get_thread_totals(thread_id)
        if existing is None:
            return
        totals = dict(existing.get("totals", {}))
        metadata = dict(existing.get("metadata", {}))
        totals["ended_at"] = _utc_now_iso()
        totals["exit_reason"] = exit_reason
        self._store.upsert_thread_totals(thread_id, totals, metadata, totals["ended_at"])
        if self._config.write_session_summary_view:
            write_thread_views(self._store, thread_id)

    def _update_thread_totals(
        self,
        *,
        context: TraceContext,
        assistant_output_preview: str | None,
        ended_at: str,
        exit_reason: str | None,
        diagnostics: dict[str, Any],
    ) -> None:
        existing = self._store.get_thread_totals(context.thread_id)
        totals = dict((existing or {}).get("totals", {}))
        metadata = dict((existing or {}).get("metadata", {}))

        totals.setdefault("thread_id", context.thread_id)
        totals["trace_count"] = int(totals.get("trace_count", 0) or 0) + 1
        totals["turn_count"] = int(totals.get("turn_count", 0) or 0) + 1
        totals["total_input_tokens"] = int(totals.get("total_input_tokens", 0) or 0) + int(context.usage.get("input_tokens", 0) or 0)
        totals["total_output_tokens"] = int(totals.get("total_output_tokens", 0) or 0) + int(context.usage.get("output_tokens", 0) or 0)
        totals["total_tokens"] = int(totals.get("total_tokens", 0) or 0) + int(context.usage.get("total_tokens", 0) or 0)
        totals["total_billable_input_tokens"] = int(totals.get("total_billable_input_tokens", 0) or 0) + int(context.usage.get("billable_input_tokens", 0) or 0)
        totals["total_prompt_cache_hit_tokens"] = int(totals.get("total_prompt_cache_hit_tokens", 0) or 0) + int(context.usage.get("prompt_cache_hit_tokens", 0) or 0)
        totals["total_prompt_cache_miss_tokens"] = int(totals.get("total_prompt_cache_miss_tokens", 0) or 0) + int(context.usage.get("prompt_cache_miss_tokens", 0) or 0)
        totals["tool_call_count"] = int(totals.get("tool_call_count", 0) or 0) + len(context.tool_calls)
        failure_summary = self._build_failure_summary(context=context, completed=context.completed)
        totals["failed_tool_call_count"] = int(totals.get("failed_tool_call_count", 0) or 0) + failure_summary["failed_tool_call_count"]
        totals["trace_with_failures_count"] = int(totals.get("trace_with_failures_count", 0) or 0) + (
            1 if failure_summary["had_any_failure"] else 0
        )
        totals["recovered_trace_count"] = int(totals.get("recovered_trace_count", 0) or 0) + (
            1 if failure_summary["recovered_after_failure"] else 0
        )
        top_failed_tools = totals.get("top_failed_tools")
        if not isinstance(top_failed_tools, dict):
            top_failed_tools = {}
        for event in failure_summary["failure_events"]:
            tool_name = str(event.get("tool_name") or "unknown_tool")
            top_failed_tools[tool_name] = int(top_failed_tools.get(tool_name, 0) or 0) + 1
        totals["top_failed_tools"] = top_failed_tools
        totals["last_failed_tools"] = failure_summary["failed_tool_names"]
        totals["last_assistant_output_preview"] = assistant_output_preview
        totals["ended_at"] = ended_at
        totals["exit_reason"] = exit_reason
        totals["last_trace_elapsed_ms"] = diagnostics["elapsed_ms"]
        totals["last_trace_billable_input_tokens"] = diagnostics["billable_input_tokens"]
        totals["last_trace_prompt_cache_hit_rate"] = diagnostics["prompt_cache_hit_rate"]
        totals["last_trace_had_any_failure"] = failure_summary["had_any_failure"]
        totals["last_trace_recovered_after_failure"] = failure_summary["recovered_after_failure"]
        totals["slow_trace_count"] = int(totals.get("slow_trace_count", 0) or 0) + (1 if diagnostics["is_slow"] else 0)
        totals["expensive_trace_count"] = int(totals.get("expensive_trace_count", 0) or 0) + (1 if diagnostics["is_expensive"] else 0)
        totals["low_cache_trace_count"] = int(totals.get("low_cache_trace_count", 0) or 0) + (1 if diagnostics["has_low_cache_hit_rate"] else 0)
        denominator = totals["total_prompt_cache_hit_tokens"] + totals["total_prompt_cache_miss_tokens"]
        totals["prompt_cache_hit_rate"] = (
            totals["total_prompt_cache_hit_tokens"] / denominator if denominator > 0 else None
        )

        metadata.setdefault("initial_agent_name", context.agent_name)
        metadata.setdefault("initial_model_name", context.model_name)
        metadata.setdefault("started_at", context.started_at)
        metadata["last_trace_id"] = context.trace_id
        metadata["content_mode"] = context.content_mode
        metadata["last_trace_file_written"] = context.trace_file_written
        metadata["last_trace_file_reason"] = context.trace_file_reason

        self._store.upsert_thread_totals(context.thread_id, totals, metadata, ended_at)

    def _export_trajectory(self, trace_payload: dict[str, Any]) -> None:
        path = (
            self._store.trajectory_success_path
            if trace_payload.get("completed")
            else self._store.trajectory_failed_path
        )
        payload = {
            "trajectory_id": trace_payload["trace_id"],
            "thread_id": trace_payload["thread_id"],
            "trace_id": trace_payload["trace_id"],
            "turn_ids": [trace_payload["trace_id"]],
            "user_goal": trace_payload.get("user_input_preview"),
            "messages": [
                {"role": "user", "content": trace_payload.get("user_input_preview")},
                {"role": "assistant", "content": trace_payload.get("assistant_output_preview")},
            ],
            "tool_calls": trace_payload.get("tool_calls", []),
            "final_answer": trace_payload.get("assistant_output_preview"),
            "completed": trace_payload.get("completed", False),
            "failure_reason": trace_payload.get("failure_reason"),
            "token_usage": trace_payload.get("usage", {}),
            "cache_summary": {
                "prompt_cache_hit_tokens": trace_payload.get("usage", {}).get("prompt_cache_hit_tokens", 0),
                "prompt_cache_miss_tokens": trace_payload.get("usage", {}).get("prompt_cache_miss_tokens", 0),
                "prompt_cache_hit_rate": trace_payload.get("usage", {}).get("prompt_cache_hit_rate"),
            },
            "elapsed_ms": trace_payload.get("elapsed_ms"),
            "created_at": trace_payload.get("ended_at") or trace_payload.get("started_at"),
        }
        append_jsonl(path, payload)
        self._store.insert_trajectory_export(
            {
                "trace_id": trace_payload["trace_id"],
                "export_target": str(path),
                "completed": trace_payload.get("completed", False),
                "created_at": payload["created_at"],
                "payload": payload,
            }
        )

    def _write_trace_file(self, payload: dict[str, Any]) -> Path:
        path = self._store.trace_file_path(
            thread_id=payload["thread_id"],
            trace_id=payload["trace_id"],
            started_at=payload["started_at"],
        )
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _build_diagnostics(self, *, context: TraceContext, elapsed_ms: int) -> dict[str, Any]:
        billable_input_tokens = int(context.usage.get("billable_input_tokens", 0) or 0)
        prompt_cache_hit_rate = context.usage.get("prompt_cache_hit_rate")
        is_slow = elapsed_ms >= self._config.slow_trace_ms
        is_expensive = billable_input_tokens >= self._config.high_billable_input_tokens
        has_low_cache_hit_rate = (
            prompt_cache_hit_rate is not None and prompt_cache_hit_rate < self._config.low_cache_hit_rate
        )
        reasons: list[str] = []
        if is_slow:
            reasons.append("slow_trace")
        if is_expensive:
            reasons.append("high_billable_input_tokens")
        if has_low_cache_hit_rate:
            reasons.append("low_cache_hit_rate")
        return {
            "elapsed_ms": elapsed_ms,
            "billable_input_tokens": billable_input_tokens,
            "prompt_cache_hit_rate": prompt_cache_hit_rate,
            "is_slow": is_slow,
            "is_expensive": is_expensive,
            "has_low_cache_hit_rate": has_low_cache_hit_rate,
            "reasons": reasons,
        }

    def _trace_file_reason(
        self,
        *,
        context: TraceContext,
        diagnostics: dict[str, Any],
        completed: bool,
    ) -> str | None:
        if context.content_mode == "full" and self._config.write_trace_files_on_full:
            return "content_mode_full"
        if not completed and self._config.write_trace_files_on_failure:
            return "trace_failed"
        if diagnostics["reasons"] and self._config.write_trace_files_on_anomaly:
            return ",".join(diagnostics["reasons"])
        return None

    @staticmethod
    def _build_failure_summary(*, context: TraceContext, completed: bool) -> dict[str, Any]:
        failed_calls = [call for call in context.tool_calls if _tool_call_failed(call)]
        failed_tool_names: list[str] = []
        seen_tool_names: set[str] = set()
        failure_events: list[dict[str, Any]] = []

        for call in failed_calls:
            tool_name = str(call.get("tool_name") or "unknown_tool")
            if tool_name not in seen_tool_names:
                failed_tool_names.append(tool_name)
                seen_tool_names.add(tool_name)
            failure_events.append(
                {
                    "tool_name": tool_name,
                    "tool_call_id": call.get("tool_call_id"),
                    "error_type": call.get("error_type"),
                    "elapsed_ms": call.get("elapsed_ms"),
                    "status": call.get("status"),
                }
            )

        had_any_failure = bool(failed_calls)
        return {
            "had_any_failure": had_any_failure,
            "failed_tool_call_count": len(failed_calls),
            "failed_tool_names": failed_tool_names,
            "recovered_after_failure": had_any_failure and completed,
            "failure_events": failure_events,
        }

    def _bootstrap_legacy_session_logs(self) -> None:
        session_logs_dir = self._store.root / "session_logs"
        if not session_logs_dir.exists():
            return
        for path in sorted(session_logs_dir.glob("*.json")):
            if self._store.has_legacy_import(str(path)):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("Skipping unreadable legacy session log: %s", path)
                continue
            thread_id = str(payload.get("initial_thread_id") or payload.get("session_id") or path.stem)
            legacy_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            legacy_turns = payload.get("turns") if isinstance(payload.get("turns"), list) else []
            if not legacy_summary and not legacy_turns:
                continue

            existing = self._store.get_thread_totals(thread_id)
            totals = dict((existing or {}).get("totals", {}))
            metadata = dict((existing or {}).get("metadata", {}))
            if not totals:
                totals = {
                    "thread_id": thread_id,
                    "trace_count": 0,
                    "turn_count": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_tokens": 0,
                    "total_billable_input_tokens": 0,
                    "total_prompt_cache_hit_tokens": 0,
                    "total_prompt_cache_miss_tokens": 0,
                    "tool_call_count": 0,
                    "failed_tool_call_count": 0,
                    "trace_with_failures_count": 0,
                    "recovered_trace_count": 0,
                    "top_failed_tools": {},
                    "last_failed_tools": [],
                }

            totals["turn_count"] = int(totals.get("turn_count", 0) or 0) + int(legacy_summary.get("turn_count", len(legacy_turns)) or 0)
            totals["total_input_tokens"] = int(totals.get("total_input_tokens", 0) or 0) + int(legacy_summary.get("total_input_tokens", 0) or 0)
            totals["total_output_tokens"] = int(totals.get("total_output_tokens", 0) or 0) + int(legacy_summary.get("total_output_tokens", 0) or 0)
            totals["total_tokens"] = int(totals.get("total_tokens", 0) or 0) + int(legacy_summary.get("total_tokens", 0) or 0)
            totals["total_billable_input_tokens"] = int(totals.get("total_billable_input_tokens", 0) or 0) + int(legacy_summary.get("total_billable_input_tokens", 0) or 0)
            totals["total_prompt_cache_hit_tokens"] = int(totals.get("total_prompt_cache_hit_tokens", 0) or 0) + int(legacy_summary.get("total_prompt_cache_hit_tokens", 0) or 0)
            totals["total_prompt_cache_miss_tokens"] = int(totals.get("total_prompt_cache_miss_tokens", 0) or 0) + int(legacy_summary.get("total_prompt_cache_miss_tokens", 0) or 0)
            denominator = totals["total_prompt_cache_hit_tokens"] + totals["total_prompt_cache_miss_tokens"]
            totals["prompt_cache_hit_rate"] = (
                totals["total_prompt_cache_hit_tokens"] / denominator if denominator > 0 else None
            )
            metadata.setdefault("initial_agent_name", payload.get("initial_agent_name"))
            metadata.setdefault("initial_model_name", payload.get("initial_model_name"))
            metadata.setdefault("started_at", payload.get("started_at"))

            self._store.upsert_thread_totals(thread_id, totals, metadata, _utc_now_iso())
            self._store.record_legacy_import(str(path), thread_id, _utc_now_iso(), payload)

    @staticmethod
    def _merge_usage(target: dict[str, Any], usage: dict[str, Any]) -> None:
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "billable_input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        ):
            target[key] = int(target.get(key, 0) or 0) + int(usage.get(key, 0) or 0)
        denominator = target["prompt_cache_hit_tokens"] + target["prompt_cache_miss_tokens"]
        target["prompt_cache_hit_rate"] = (
            target["prompt_cache_hit_tokens"] / denominator if denominator > 0 else None
        )
        target["cache_signal_available"] = bool(
            target["prompt_cache_hit_tokens"] or target["prompt_cache_miss_tokens"] or target.get("cache_read_input_tokens")
        )


def _tool_call_failed(call: dict[str, Any]) -> bool:
    status = str(call.get("status") or "ok").lower()
    if status not in {"ok", "success"}:
        return True
    return _tool_result_preview_looks_failed(call.get("result_preview"))


def _tool_result_preview_looks_failed(result_preview: Any) -> bool:
    if result_preview is None:
        return False
    text = str(result_preview).lstrip().lower()
    return text.startswith(
        (
            "error:",
            "task failed.",
            "task timed out.",
            "task polling timed out",
            "task cancelled",
        )
    )


def _load_config() -> ObservabilityConfig:
    app_config = get_app_config()
    cfg = getattr(app_config, "observability", None)
    if isinstance(cfg, ObservabilityConfig):
        return cfg
    if isinstance(cfg, dict):
        return ObservabilityConfig(**cfg)
    return ObservabilityConfig()


def _empty_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "billable_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "prompt_cache_hit_rate": None,
        "cache_signal_available": False,
    }


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
