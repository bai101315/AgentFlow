"""Patched ChatDeepSeek that preserves reasoning_content in multi-turn conversations.

This module provides a patched version of ChatDeepSeek that properly handles
reasoning_content when sending messages back to the API. The original implementation
stores reasoning_content in additional_kwargs but doesn't include it when making
subsequent API calls, which causes errors with APIs that require reasoning_content
on all assistant messages when thinking mode is enabled.
"""

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_deepseek import ChatDeepSeek


def _strip_cache_control(value: Any) -> Any:
    """Remove Anthropic-style cache_control markers from DeepSeek payloads."""
    if isinstance(value, dict):
        return {key: _strip_cache_control(inner) for key, inner in value.items() if key != "cache_control"}
    if isinstance(value, list):
        return [_strip_cache_control(item) for item in value]
    return value


class PatchedChatDeepSeek(ChatDeepSeek):
    """ChatDeepSeek with proper reasoning_content preservation.

    When using thinking/reasoning enabled models, the API expects reasoning_content
    to be present on ALL assistant messages in multi-turn conversations. This patched
    version ensures reasoning_content from additional_kwargs is included in the
    request payload.
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {"api_key": "DEEPSEEK_API_KEY", "openai_api_key": "DEEPSEEK_API_KEY"}

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get request payload with reasoning_content preserved.

        Overrides the parent method to inject reasoning_content from
        additional_kwargs into assistant messages in the payload.
        """
        # Get the original messages before conversion
        original_messages = self._convert_input(input_).to_messages()

        # Call parent to get the base payload
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload = _strip_cache_control(payload)

        # Match payload messages with original messages to restore reasoning_content
        payload_messages = payload.get("messages", [])

        # The payload messages and original messages should be in the same order
        # Iterate through both and match by position
        if len(payload_messages) == len(original_messages):
            for payload_msg, orig_msg in zip(payload_messages, original_messages):
                if payload_msg.get("role") == "assistant" and isinstance(orig_msg, AIMessage):
                    reasoning_content = orig_msg.additional_kwargs.get("reasoning_content")
                    if reasoning_content is not None:
                        payload_msg["reasoning_content"] = reasoning_content
        else:
            # Fallback: match by counting assistant messages
            ai_messages = [m for m in original_messages if isinstance(m, AIMessage)]
            assistant_payloads = [(i, m) for i, m in enumerate(payload_messages) if m.get("role") == "assistant"]

            for (idx, payload_msg), ai_msg in zip(assistant_payloads, ai_messages):
                reasoning_content = ai_msg.additional_kwargs.get("reasoning_content")
                if reasoning_content is not None:
                    payload_messages[idx]["reasoning_content"] = reasoning_content

        return payload

    def _create_chat_result(self, response: dict | Any, generation_info: dict | None = None):
        """Create chat result and surface DeepSeek cache usage metadata."""
        result = super()._create_chat_result(response, generation_info=generation_info)

        response_dict = response if isinstance(response, dict) else getattr(response, "model_dump", lambda: {})()
        usage = response_dict.get("usage") if isinstance(response_dict, dict) else None
        if not isinstance(usage, dict):
            return result

        cache_usage = {
            key: usage.get(key)
            for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
            if usage.get(key) is not None
        }
        if not cache_usage:
            return result

        for generation in result.generations:
            message = generation.message
            if isinstance(message, AIMessage):
                response_metadata = dict(message.response_metadata or {})
                token_usage = dict(response_metadata.get("token_usage") or {})
                token_usage.update(cache_usage)
                response_metadata["token_usage"] = token_usage
                message.response_metadata = response_metadata

                usage_metadata = dict(message.usage_metadata or {})
                usage_metadata.update(cache_usage)
                message.usage_metadata = usage_metadata

        return result
