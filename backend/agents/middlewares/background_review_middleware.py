"""Background self-improvement review middleware."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from config import get_app_config
from config.self_improvement_config import BackgroundReviewConfig
from models import create_chat_model
from tools.skill_manage_tool import _skill_manage_impl

logger = logging.getLogger(__name__)

BACKGROUND_REVIEW_ORIGIN = "background_review"

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)

_REVIEW_SYSTEM_PROMPT = """You are a background self-improvement reviewer.
You are isolated from the foreground conversation and must only propose durable skill updates.
Return strict JSON only. Do not include prose outside JSON.

Allowed actions:
- create_skill: {"action":"create_skill","name":"class-level-name","content":"full SKILL.md"}
- patch_skill: {"action":"patch_skill","name":"existing-skill","find":"exact text","replace":"replacement","expected_count":1}
- write_support_file: {"action":"write_support_file","name":"existing-skill","path":"references/detail.md","content":"..."}
- noop: {"action":"noop","reason":"..."}

Preserve only first-class reusable signals:
- user corrections about style, tone, formatting, verbosity, response shape
- user corrections about workflow, method, step order, command choice, debugging approach
- non-trivial reusable technical workflows, repair patterns, guardrails, or pitfalls
- a loaded skill proved wrong, incomplete, stale, or missing an important step

Negative filters:
- do not save one-off task facts, transient summaries, market/news requests, PR-specific or issue-specific details
- do not generalize from missing local binaries, unavailable commands, dependency drift, or post-migration breakage
- do not save broad claims that a tool or feature is broken
- do not save transient errors solved in the same conversation unless the durable lesson is the retry/recovery pattern

Action priority:
1. patch an existing relevant custom skill
2. write a support file under references/, templates/, scripts/, or assets/
3. create a new class-level umbrella skill only when no existing custom skill covers the workflow

Skill names must be class-level hyphen-case names, not PR numbers, error strings, codenames, or one-off task names.
Respond as {"actions":[...]} with at most the requested number of actions.
"""


@dataclass
class _ThreadReviewState:
    seen_tool_call_ids: set[str] = field(default_factory=set)
    tool_calls_since_skill: int = 0
    review_running: bool = False


def _extract_message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return str(content)


def _message_tool_calls(message: Any) -> list[dict[str, Any]]:
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        return [dict(call) for call in tool_calls]
    raw_calls = (getattr(message, "additional_kwargs", None) or {}).get("tool_calls") or []
    parsed: list[dict[str, Any]] = []
    for raw in raw_calls:
        function = raw.get("function") if isinstance(raw, dict) else None
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        arguments = function.get("arguments") or "{}"
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            args = {}
        parsed.append({"id": raw.get("id"), "name": name, "args": args})
    return parsed


def _tool_call_key(thread_id: str, ordinal: int, call: dict[str, Any]) -> str:
    call_id = call.get("id")
    if call_id:
        return str(call_id)
    return f"{thread_id}:{ordinal}:{call.get('name')}:{json.dumps(call.get('args', {}), sort_keys=True, default=str)}"


def _recent_transcript(messages: list[Any], *, max_messages: int) -> str:
    recent = messages[-max_messages:]
    lines: list[str] = []
    for msg in recent:
        msg_type = getattr(msg, "type", None)
        if msg_type == "human":
            role = "user"
        elif msg_type == "ai":
            role = "assistant"
        elif msg_type == "tool":
            role = f"tool:{getattr(msg, 'name', '') or getattr(msg, 'tool_call_id', '')}"
        else:
            continue
        text = _extract_message_text(msg).strip()
        if not text and not _message_tool_calls(msg):
            continue
        if _message_tool_calls(msg):
            calls = ", ".join(call.get("name", "?") for call in _message_tool_calls(msg))
            text = f"{text}\n[tool_calls: {calls}]".strip()
        lines.append(f"{role}: {text}")
    return "\n\n".join(lines)


def _parse_actions(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    match = _JSON_BLOCK_RE.search(raw)
    if match:
        raw = match.group(1)
    data = json.loads(raw)
    if isinstance(data, list):
        actions = data
    else:
        actions = data.get("actions", [])
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


async def apply_review_actions(
    actions: list[dict[str, Any]],
    *,
    thread_id: str | None,
    max_actions: int,
) -> list[str]:
    """Apply background review actions through skill_manage."""
    runtime = SimpleNamespace(context={"thread_id": thread_id} if thread_id else {}, config={"configurable": {"thread_id": thread_id} if thread_id else {}})
    summaries: list[str] = []
    for action in actions[:max_actions]:
        action_name = action.get("action")
        if action_name == "noop":
            continue
        try:
            if action_name == "create_skill":
                result = await _skill_manage_impl(
                    runtime=runtime,
                    action="create",
                    name=str(action.get("name") or ""),
                    content=action.get("content"),
                    origin=BACKGROUND_REVIEW_ORIGIN,
                )
            elif action_name == "patch_skill":
                result = await _skill_manage_impl(
                    runtime=runtime,
                    action="patch",
                    name=str(action.get("name") or ""),
                    find=action.get("find"),
                    replace=action.get("replace"),
                    expected_count=action.get("expected_count"),
                    origin=BACKGROUND_REVIEW_ORIGIN,
                )
            elif action_name == "write_support_file":
                result = await _skill_manage_impl(
                    runtime=runtime,
                    action="write_file",
                    name=str(action.get("name") or ""),
                    path=action.get("path"),
                    content=action.get("content"),
                    origin=BACKGROUND_REVIEW_ORIGIN,
                )
            else:
                continue
            summaries.append(result)
        except Exception as exc:
            logger.warning("Background review action failed: %s", exc, exc_info=True)
    return summaries


class BackgroundReviewMiddleware(AgentMiddleware[AgentState]):
    """Trigger daemon skill review after enough new tool calls."""

    state_schema = AgentState

    def __init__(
        self,
        *,
        reviewer: Callable[[str, BackgroundReviewConfig], list[dict[str, Any]]] | None = None,
        inline: bool = False,
    ):
        super().__init__()
        self._states: dict[str, _ThreadReviewState] = defaultdict(_ThreadReviewState)
        self._lock = threading.Lock()
        self._reviewer = reviewer
        self._inline = inline

    def _thread_id(self, runtime: Runtime) -> str | None:
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            config_data = get_config()
            thread_id = config_data.get("configurable", {}).get("thread_id")
        return thread_id

    def _collect_new_tool_calls(self, thread_id: str, messages: list[Any]) -> list[dict[str, Any]]:
        new_calls: list[dict[str, Any]] = []
        state = self._states[thread_id]
        for ordinal, msg in enumerate(messages):
            for call in _message_tool_calls(msg):
                key = _tool_call_key(thread_id, ordinal, call)
                if key in state.seen_tool_call_ids:
                    continue
                state.seen_tool_call_ids.add(key)
                new_calls.append(call)
        return new_calls

    def _model_review(self, transcript: str, config: BackgroundReviewConfig) -> list[dict[str, Any]]:
        model = create_chat_model(name=config.review_model_name, thinking_enabled=False)
        response = model.invoke(
            [
                SystemMessage(content=_REVIEW_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"Max actions: {config.max_actions_per_review}\n\n"
                        "Review this recent conversation transcript for durable skill self-improvement actions.\n\n"
                        f"{transcript}"
                    )
                ),
            ]
        )
        return _parse_actions(str(getattr(response, "content", "")))

    def _run_review(self, *, thread_id: str, messages: list[Any], config: BackgroundReviewConfig) -> None:
        try:
            transcript = _recent_transcript(messages, max_messages=config.max_messages)
            if not transcript.strip():
                return
            actions = self._reviewer(transcript, config) if self._reviewer else self._model_review(transcript, config)
            asyncio.run(apply_review_actions(actions, thread_id=thread_id, max_actions=config.max_actions_per_review))
        except Exception:
            logger.exception("Background review failed")
        finally:
            with self._lock:
                self._states[thread_id].review_running = False

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        app_config = get_app_config()
        review_config = app_config.background_review
        if not review_config.enabled or not app_config.skill_evolution.enabled:
            return None
        if review_config.skill_nudge_interval <= 0:
            return None

        thread_id = self._thread_id(runtime)
        messages = state.get("messages", [])
        if not thread_id or not messages:
            return None

        with self._lock:
            thread_state = self._states[thread_id]
            new_calls = self._collect_new_tool_calls(thread_id, list(messages))
            if not new_calls:
                return None

            if any(call.get("name") == "skill_manage" for call in new_calls):
                thread_state.tool_calls_since_skill = 0
                return None

            thread_state.tool_calls_since_skill += len(new_calls)
            if thread_state.tool_calls_since_skill < review_config.skill_nudge_interval:
                return None
            if thread_state.review_running:
                return None
            thread_state.tool_calls_since_skill = 0
            thread_state.review_running = True

        kwargs = {"thread_id": thread_id, "messages": list(messages), "config": review_config}
        if self._inline:
            self._run_review(**kwargs)
        else:
            threading.Thread(
                target=self._run_review,
                kwargs=kwargs,
                name=f"background-review-{thread_id}",
                daemon=True,
            ).start()
        return None
