"""Prompt caching middleware for a single frozen session prompt."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.runtime import Runtime

from config.prompt_caching_config import get_prompt_caching_config

logger = logging.getLogger(__name__)

PromptBuilder = Callable[[], str]
PROMPT_CACHE_PLACEHOLDER = "Session prompt is injected by PromptCacheMiddleware."


class PromptCacheMiddlewareState(AgentState):
    """Runtime state fields used by prompt caching."""


def build_prompt_cache_signature(
    *,
    model_name: str | None,
    model_config: Any,
    agent_name: str | None,
    agent_config: Any,
    subagent_enabled: bool,
    max_concurrent_subagents: int,
    is_bootstrap: bool,
) -> str:
    payload = {
        "version": 1,
        "model_name": model_name,
        "provider_use": getattr(model_config, "use", None),
        "provider_model": getattr(model_config, "model", None),
        "agent_name": agent_name or "default",
        "agent_model": getattr(agent_config, "model", None) if agent_config else None,
        "tool_groups": getattr(agent_config, "tool_groups", None) if agent_config else None,
        "skills": sorted(getattr(agent_config, "skills", None) or []) if agent_config else None,
        "subagent_enabled": subagent_enabled,
        "max_concurrent_subagents": max_concurrent_subagents,
        "is_bootstrap": is_bootstrap,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PromptCacheMiddleware(AgentMiddleware[PromptCacheMiddlewareState]):
    """Freeze one session prompt per thread."""

    state_schema = PromptCacheMiddlewareState

    def __init__(
        self,
        *,
        prompt_builder: PromptBuilder,
        signature: str,
        model_name: str | None,
    ) -> None:
        self._prompt_builder = prompt_builder
        self._signature = signature
        self._model_name = model_name or ""

    def _get_frozen_prompt(self, state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        cached_prompt = state.get("cached_system_prompt")
        cached_signature = state.get("cached_system_prompt_signature")
        if cached_prompt and cached_signature == self._signature:
            return cached_prompt, {}

        prompt = self._prompt_builder()
        updates = {
            "cached_system_prompt": prompt,
            "cached_system_prompt_signature": self._signature,
        }
        return prompt, updates

    def _prepare_request(self, request: ModelRequest) -> tuple[ModelRequest, dict[str, Any]]:
        config = get_prompt_caching_config()
        if not config.enabled:
            return request, {}

        frozen_prompt, updates = self._get_frozen_prompt(request.state)
        request = request.override(system_message=SystemMessage(content=frozen_prompt))
        return request, updates

    def _extract_usage_dict(self, message: AIMessage) -> dict[str, Any]:
        usage_metadata = getattr(message, "usage_metadata", None)
        response_metadata = getattr(message, "response_metadata", None)
        candidates: list[Any] = [usage_metadata, response_metadata]
        if isinstance(response_metadata, dict):
            candidates.extend([response_metadata.get("token_usage"), response_metadata.get("usage")])

        for candidate in candidates:
            if isinstance(candidate, dict) and (
                "prompt_cache_hit_tokens" in candidate
                or "prompt_cache_miss_tokens" in candidate
                or "cache_read_input_tokens" in candidate
                or "cache_creation_input_tokens" in candidate
            ):
                return candidate
        return {}

    def _log_cache_usage(self, response: ModelResponse) -> None:
        config = get_prompt_caching_config()
        if not config.enabled or not config.log_usage:
            return

        for item in response.result:
            if not isinstance(item, AIMessage):
                continue
            usage = self._extract_usage_dict(item)
            if not usage:
                continue
            logger.info("Prompt cache usage: model=%s usage=%s", self._model_name or "default", usage)
            return

    @override
    def before_model(self, state: PromptCacheMiddlewareState, runtime: Runtime) -> dict | None:
        config = get_prompt_caching_config()
        if not config.enabled:
            return None
        _, updates = self._get_frozen_prompt(state)
        if updates:
            logger.info(
                "Frozen system prompt created: signature=%s prompt_sha256=%s",
                self._signature,
                hashlib.sha256(updates["cached_system_prompt"].encode("utf-8")).hexdigest()[:16],
            )
        return updates or None

    @override
    async def abefore_model(self, state: PromptCacheMiddlewareState, runtime: Runtime) -> dict | None:
        return self.before_model(state, runtime)

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        prepared_request, _updates = self._prepare_request(request)
        response = handler(prepared_request)
        self._log_cache_usage(response)
        return response

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> ModelResponse:
        prepared_request, _updates = self._prepare_request(request)
        response = await handler(prepared_request)
        self._log_cache_usage(response)
        return response
