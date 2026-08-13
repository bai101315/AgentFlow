"""Background self-improvement review trigger.

This middleware only *decides when* to review; the review itself runs in the
isolated runtime under ``agents/review_agent``.  It must never extend the
foreground tool loop or change the current turn's result (§3.2), so
``after_agent`` does bookkeeping and returns ``None`` immediately.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, override

from agents.review_agent.runtime import ReviewRequest, ReviewResult, get_review_scheduler
from config import get_app_config
from config.self_improvement_config import BackgroundReviewConfig
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime
from skill.usage import ORIGIN_BACKGROUND_REVIEW

logger = logging.getLogger(__name__)

BACKGROUND_REVIEW_ORIGIN = ORIGIN_BACKGROUND_REVIEW

# Per-thread state is kept in memory for the life of the process, so cap how
# many tool-call keys a single thread can accumulate (§2.6) and drop threads
# that have gone quiet.
_MAX_SEEN_TOOL_CALLS_PER_THREAD = 2000
_THREAD_STATE_TTL_SECONDS = 24 * 3600


@dataclass
class _ThreadReviewState:
    seen_tool_call_ids: set[str] = field(default_factory=set)
    tool_calls_since_skill: int = 0
    review_running: bool = False
    last_seen_at: float = field(default_factory=time.time)


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


def build_transcript(messages: list[Any], *, max_messages: int) -> str:
    """Render an immutable text snapshot of the recent conversation.

    A plain string is handed to the reviewer instead of the live message
    objects: it cannot be mutated by the foreground turn while the review runs,
    and it cannot smuggle tool-call state into the review agent (§6.4).
    """
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
        calls = _message_tool_calls(msg)
        if not text and not calls:
            continue
        if calls:
            call_names = ", ".join(call.get("name", "?") for call in calls)
            text = f"{text}\n[tool_calls: {call_names}]".strip()
        lines.append(f"{role}: {text}")
    return "\n\n".join(lines)


class BackgroundReviewMiddleware(AgentMiddleware[AgentState]):
    """Trigger an isolated skill review after enough new tool calls."""

    state_schema = AgentState

    def __init__(self, *, scheduler=None):
        super().__init__()
        self._states: dict[str, _ThreadReviewState] = defaultdict(_ThreadReviewState)
        self._lock = threading.Lock()
        self._scheduler = scheduler or get_review_scheduler()

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
        if len(state.seen_tool_call_ids) > _MAX_SEEN_TOOL_CALLS_PER_THREAD:
            # Only the tail matters for dedupe; older ids can no longer reappear
            # because the transcript window has moved past them.
            state.seen_tool_call_ids = set(list(state.seen_tool_call_ids)[-_MAX_SEEN_TOOL_CALLS_PER_THREAD:])
        return new_calls

    def _prune_stale_threads(self, *, now: float) -> None:
        stale = [
            thread_id
            for thread_id, state in self._states.items()
            if not state.review_running and now - state.last_seen_at > _THREAD_STATE_TTL_SECONDS
        ]
        for thread_id in stale:
            self._states.pop(thread_id, None)

    def _on_review_done(self, thread_id: str, result: ReviewResult | None) -> None:
        with self._lock:
            state = self._states.get(thread_id)
            if state is not None:
                state.review_running = False
        if result is None:
            return
        notifications = get_app_config().background_review.notifications
        if notifications != "off" and result.applied:
            logger.info(
                "Background review applied %d skill change(s) for thread %s: %s",
                len(result.applied),
                thread_id,
                "; ".join(result.applied),
            )

    def _build_request(
        self,
        *,
        thread_id: str,
        messages: list[Any],
        config: BackgroundReviewConfig,
        agent_name: str | None,
    ) -> ReviewRequest | None:
        transcript = build_transcript(messages, max_messages=config.max_messages)
        if not transcript.strip():
            return None
        return ReviewRequest(
            transcript=transcript,
            parent_thread_id=thread_id,
            max_actions=config.max_actions_per_review,
            timeout_seconds=config.timeout_seconds,
            review_model_name=config.review_model_name,
            agent_name=agent_name,
        )

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        app_config = get_app_config()
        review_config = app_config.background_review
        if not review_config.enabled or not app_config.skill_evolution.enabled:
            return None
        if review_config.skill_nudge_interval <= 0:
            return None

        thread_id = self._thread_id(runtime)
        agent_name = None
        if runtime.context:
            agent_name = runtime.context.get("agent_name")
        if agent_name is None:
            try:
                agent_name = get_config().get("configurable", {}).get("agent_name")
            except RuntimeError:
                agent_name = None
        messages = state.get("messages", [])
        if not thread_id or not messages:
            return None

        # Snapshot the messages inside the lock, then do all model work off the
        # foreground path.
        with self._lock:
            now = time.time()
            self._prune_stale_threads(now=now)
            thread_state = self._states[thread_id]
            thread_state.last_seen_at = now
            new_calls = self._collect_new_tool_calls(thread_id, list(messages))
            if not new_calls:
                return None

            if any(call.get("name") == "skill_manage" for call in new_calls):
                # The agent just wrote a skill itself; the nudge is unnecessary.
                thread_state.tool_calls_since_skill = 0
                return None

            thread_state.tool_calls_since_skill += len(new_calls)
            if thread_state.tool_calls_since_skill < review_config.skill_nudge_interval:
                return None
            if thread_state.review_running:
                # One review per thread at a time; this trigger is dropped
                # rather than queued (§6.5).
                return None

            request = self._build_request(
                thread_id=thread_id,
                messages=list(messages),
                config=review_config,
                agent_name=agent_name,
            )
            if request is None:
                return None

            thread_state.tool_calls_since_skill = 0
            thread_state.review_running = True

        submitted = self._scheduler.submit(
            request,
            max_concurrent=review_config.max_concurrent_reviews,
            on_done=lambda result: self._on_review_done(thread_id, result),
        )
        if not submitted:
            logger.debug("Background review for thread %s deferred: concurrency limit reached", thread_id)
            from skill.events import emit_event

            emit_event(
                "review_dropped",
                status="dropped",
                thread_id=thread_id,
                parent_thread_id=thread_id,
                agent_name=agent_name,
                reason="concurrency_limit",
            )
        return None
