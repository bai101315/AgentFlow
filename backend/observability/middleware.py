from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from .recorder import get_current_trace, get_observability_recorder


class ObservabilityMiddleware(AgentMiddleware[AgentState]):
    """Capture model-call and tool-call observability with minimal coupling."""

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        context = get_current_trace()
        if context is None:
            return handler(request)

        started_perf = time.perf_counter()
        started_at = _utc_now_iso()
        response = handler(request)
        elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
        ended_at = _utc_now_iso()

        model_name = _resolve_model_name(request)
        for item in response.result:
            if isinstance(item, AIMessage):
                usage = _extract_usage_dict(item)
                preview_text = _message_text(item.content)
                get_observability_recorder().record_model_call(
                    trace_id=context.trace_id,
                    model_name=model_name,
                    usage=usage,
                    preview_text=preview_text,
                    started_at=started_at,
                    ended_at=ended_at,
                    elapsed_ms=elapsed_ms,
                )
        return response

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]) -> ModelResponse:
        context = get_current_trace()
        if context is None:
            return await handler(request)

        started_perf = time.perf_counter()
        started_at = _utc_now_iso()
        response = await handler(request)
        elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
        ended_at = _utc_now_iso()

        model_name = _resolve_model_name(request)
        for item in response.result:
            if isinstance(item, AIMessage):
                usage = _extract_usage_dict(item)
                preview_text = _message_text(item.content)
                get_observability_recorder().record_model_call(
                    trace_id=context.trace_id,
                    model_name=model_name,
                    usage=usage,
                    preview_text=preview_text,
                    started_at=started_at,
                    ended_at=ended_at,
                    elapsed_ms=elapsed_ms,
                )
        return response

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        context = get_current_trace()
        if context is None:
            return handler(request)

        started_perf = time.perf_counter()
        status = "ok"
        error_type = None
        result_value: Any = ""
        try:
            result = handler(request)
            result_value = _tool_result_value(result)
            if _tool_result_status(result) != "ok":
                status = _tool_result_status(result)
                error_type = _tool_result_error_type(result)
            return result
        except Exception as exc:
            status = "error"
            error_type = exc.__class__.__name__
            result_value = str(exc)
            raise
        finally:
            elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
            get_observability_recorder().record_tool_call(
                trace_id=context.trace_id,
                tool_call_id=str(request.tool_call.get("id") or ""),
                tool_name=str(request.tool_call.get("name") or getattr(request.tool, "name", "unknown_tool")),
                args_value=request.tool_call.get("args"),
                result_value=result_value,
                status=status,
                error_type=error_type,
                elapsed_ms=elapsed_ms,
            )

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        context = get_current_trace()
        if context is None:
            return await handler(request)

        started_perf = time.perf_counter()
        status = "ok"
        error_type = None
        result_value: Any = ""
        try:
            result = await handler(request)
            result_value = _tool_result_value(result)
            if _tool_result_status(result) != "ok":
                status = _tool_result_status(result)
                error_type = _tool_result_error_type(result)
            return result
        except Exception as exc:
            status = "error"
            error_type = exc.__class__.__name__
            result_value = str(exc)
            raise
        finally:
            elapsed_ms = int((time.perf_counter() - started_perf) * 1000)
            get_observability_recorder().record_tool_call(
                trace_id=context.trace_id,
                tool_call_id=str(request.tool_call.get("id") or ""),
                tool_name=str(request.tool_call.get("name") or getattr(request.tool, "name", "unknown_tool")),
                args_value=request.tool_call.get("args"),
                result_value=result_value,
                status=status,
                error_type=error_type,
                elapsed_ms=elapsed_ms,
            )


def _resolve_model_name(request: ModelRequest) -> str | None:
    runtime = getattr(request, "runtime", None)
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict):
            return configurable.get("model_name") or configurable.get("model")
    model = getattr(request, "model", None)
    return getattr(model, "model_name", None) or getattr(model, "model", None) or getattr(model, "name", None)


def _extract_usage_dict(message: AIMessage) -> dict[str, Any]:
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
    denominator = prompt_cache_hit_tokens + prompt_cache_miss_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "billable_input_tokens": prompt_cache_miss_tokens if prompt_cache_miss_tokens > 0 else max(0, input_tokens - prompt_cache_hit_tokens),
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": cache_creation_tokens,
        "prompt_cache_hit_tokens": prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": prompt_cache_miss_tokens,
        "prompt_cache_hit_rate": prompt_cache_hit_tokens / denominator if denominator > 0 else None,
        "cache_signal_available": denominator > 0 or cache_read_tokens > 0 or cache_creation_tokens > 0,
    }


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
        return "\n".join(piece for piece in pieces if piece)
    return str(content)


def _tool_result_value(result: ToolMessage | Command) -> Any:
    if isinstance(result, ToolMessage):
        return result.content
    update = getattr(result, "update", None)
    if isinstance(update, dict):
        messages = update.get("messages")
        if isinstance(messages, list):
            return [getattr(message, "content", str(message)) for message in messages]
    return str(result)


def _tool_result_status(result: ToolMessage | Command) -> str:
    if isinstance(result, ToolMessage):
        return _normalize_tool_status(getattr(result, "status", "ok"))
    update = getattr(result, "update", None)
    if isinstance(update, dict):
        messages = update.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, ToolMessage):
                    return _normalize_tool_status(getattr(message, "status", "ok"))
    return "ok"


def _tool_result_error_type(result: ToolMessage | Command) -> str | None:
    if _tool_result_status(result) == "ok" and not _tool_result_looks_failed(result):
        return None
    return "tool_result_error"


def _normalize_tool_status(status: Any) -> str:
    value = str(status or "ok").lower()
    if value in {"ok", "success"}:
        return "ok"
    return value


def _tool_result_looks_failed(result: ToolMessage | Command) -> bool:
    value = _tool_result_value(result)
    if isinstance(value, list):
        text = "\n".join(str(item) for item in value)
    else:
        text = str(value)
    text = text.lstrip().lower()
    return text.startswith(
        (
            "error:",
            "task failed.",
            "task timed out.",
            "task polling timed out",
            "task cancelled",
        )
    )


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
