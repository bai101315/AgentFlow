# ruff: noqa: E501  # prompt lines are intentionally long
"""Isolated background review runtime.

The reviewer runs as its own agent (docs/SELF_IMPROVING_MIGRATION.md §6.4):

* its own ``review_thread_id``, unrelated to the foreground thread;
* ``checkpointer=None``, so no review message ever reaches the parent
  checkpoint and the parent session is never rotated;
* only skill tools registered — no bash, no network, no browser, no MCP;
* a ``skill_manage`` bound to ``origin=background_review`` that cannot delete;
* it returns an action summary, never its internal messages.

The request carries an immutable transcript snapshot rather than the live
foreground ``AgentState``, so the foreground turn can keep mutating its own
state while the review runs (§6.4).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from skill.events import emit_event
from skill.usage import ORIGIN_BACKGROUND_REVIEW

logger = logging.getLogger(__name__)

EXECUTION_CONTEXT_BACKGROUND_REVIEW = "background_review"

# The reviewer only needs: look at the index, read a few skills, write a few
# times. This bounds the tool loop even if the model starts churning.
_RECURSION_LIMIT_PER_ACTION = 4
_MIN_RECURSION_LIMIT = 12

REVIEW_SYSTEM_PROMPT = """You are a background self-improvement reviewer for an AI agent.

You are isolated from the foreground conversation: the user cannot see you, and nothing you say reaches them. Your only effect on the world is the skill writes you make with `skill_manage`.

Your job: read the transcript of a finished conversation and decide whether it contains a durable, reusable lesson worth writing into the skill library.

Preserve only first-class reusable signals:
- the user corrected your style, tone, formatting, verbosity, or response shape. Frustration signals like "stop doing X", "too verbose", "don't format like this", "just give me the answer", or an explicit "remember this" are FIRST-CLASS skill signals, not just memory signals — the preference must be embedded in the skill that governs the task
- the user corrected your workflow, method, step order, command choice, or debugging approach, and the corrected approach worked
- a non-trivial reusable technical workflow, repair pattern, guardrail, or pitfall emerged after meaningful tool use
- a skill that was used proved wrong, incomplete, stale, or missing an important step

Where learnings live — route each durable signal to exactly one place:
- SKILL (your writes via `skill_manage`) — HOW to do a class of task: workflows, repair patterns, pitfalls, style/verbosity preferences, guardrails, setup fixes.
- Memory (handled by the memory system, NOT by you) — WHO the user is and their current situation: persona, job, projects, tech stack, stated facts. A preference has two faces: the fact side (this user hates verbosity) lives in memory; the execution side (how to answer this user's tasks) lives in the SKILL.md body. When the user complained about HOW you handled a task, the lesson belongs in the skill, not just memory.
- Session history (automatic via session_search, never written by you) — raw conversation text. Do not try to write it.
- Nothing — environment-specific failures, transient errors resolved in-session, negative tool claims, one-off task narratives (see "Never preserve" below).

Target shape of the library: CLASS-LEVEL skills, each with a rich SKILL.md and a `references/` directory for session-specific detail. Not a long flat list of narrow one-session-one-skill entries. This shapes HOW you update, not WHETHER you update.

Never preserve:
- one-off task facts, transient summaries, market/news requests, or PR-specific and issue-specific details
- lessons generalised from missing local binaries, unavailable commands, dependency drift, or post-migration breakage
- broad claims that a tool or feature is broken
- transient errors solved within the same conversation, unless the durable lesson is the retry or recovery pattern

If a tool failed because of setup state, capture the FIX (install command, config step, env var to set) under an existing setup or troubleshooting skill — never "this tool does not work" as a standalone constraint.

Procedure:
1. Call `skills_list` to see what already exists.
2. Call `skill_view` on any skill that looks related. Never patch a skill you have not read.
3. Apply changes in this priority order:
   a. `patch` an existing relevant custom skill;
   b. `write_file` a support file under `references/`, `templates/`, `scripts/`, or `assets/`;
   c. `create` a new class-level umbrella skill only when no existing custom skill covers the workflow.
4. Stop. Reply with one short sentence describing what you changed, or exactly "noop" when there was no durable signal.

If you notice two existing skills that overlap, note it in your reply so a future curator pass can consolidate them — do not try to merge them yourself.

SKILL.md format. When you `create` or `edit`, `content` must be a full SKILL.md that starts with YAML frontmatter.
`name` must exactly match the skill name you pass to `skill_manage`.
`name` and `description` are the only required frontmatter keys; extra keys are allowed. The `description` should state when
the skill applies, because that is what a future agent matches against.

```
---
name: your-skill-name
description: Use this skill when <trigger condition>. Covers <what it does>.
---

# Your Skill Name

## When to use
...

## Steps
1. ...

## Pitfalls
...
```

If a `skill_manage` call returns an error, read the error, fix your input, and retry once. A malformed frontmatter error means your `content` was wrong, not that the action is impossible.

Constraints:
- You may apply at most {max_actions} skill write(s) this run.
- Skill names must be class-level hyphen-case names, not issue IDs, PR numbers, error strings, codenames, or one-off task names.
- You may only modify skills the review system owns. A permission error means the skill belongs to the user; do not retry it.
- Be ACTIVE: most reviews should produce at least one skill update. A pass that writes nothing is a missed learning opportunity, not a neutral outcome. Reply 'noop' only when the conversation genuinely contained no durable signal (no corrections, no reusable technique, no skill gap).
"""


@dataclass(frozen=True)
class ReviewRequest:
    """Immutable snapshot handed to the review runtime.

    Frozen on purpose: §6.4 forbids passing review context through a shared
    mutable ``AgentState``.
    """

    transcript: str
    parent_thread_id: str
    max_actions: int
    timeout_seconds: float
    review_model_name: str | None = None
    agent_name: str | None = None
    review_thread_id: str = field(default_factory=lambda: f"review-{uuid.uuid4().hex[:12]}")


@dataclass
class ReviewResult:
    status: str
    review_thread_id: str
    applied: list[str] = field(default_factory=list)
    summary: str = ""
    error: str | None = None


def build_review_tools(request: ReviewRequest, on_applied: Callable[[str], None]) -> list[Any]:
    """Return the only tools the reviewer is allowed to hold."""
    from tools.builtins.skill_tools import skill_view_tool, skills_list_tool
    from tools.skill_manage_tool import build_background_skill_manage_tool

    return [
        skills_list_tool,
        skill_view_tool,
        build_background_skill_manage_tool(
            origin=ORIGIN_BACKGROUND_REVIEW,
            execution_context=EXECUTION_CONTEXT_BACKGROUND_REVIEW,
            thread_id=request.review_thread_id,
            parent_thread_id=request.parent_thread_id,
            agent_name=request.agent_name,
            model_name=resolve_review_model_name(request.review_model_name),
            max_actions=request.max_actions,
            on_applied=on_applied,
        ),
    ]


class _ReviewToolErrorMiddleware(AgentMiddleware):
    """Return tool errors to the reviewer instead of aborting the review.

    Without this, a single rejected write (bad frontmatter, a patch whose
    ``find`` text does not match, a permission error on a user-owned skill)
    propagates out of the tool and kills the whole run, so a recoverable
    mistake is reported as ``applied: 0``.  The reviewer can read the message
    and correct itself.

    This is deliberately the *only* middleware the reviewer gets: it is
    self-contained and touches no memory, session or checkpoint state, so it
    does not weaken the isolation guarantees in §6.4.
    """

    def _error_message(self, request, exc: Exception) -> ToolMessage:
        tool_name = str(request.tool_call.get("name") or "unknown_tool")
        detail = str(exc).strip() or exc.__class__.__name__
        if len(detail) > 500:
            detail = detail[:497] + "..."
        logger.info("Background review tool '%s' failed: %s", tool_name, detail)
        return ToolMessage(
            content=(
                f"Tool '{tool_name}' failed with {exc.__class__.__name__}: {detail}\n"
                "Fix your input and retry once. If the skill belongs to the user or the action "
                "is not permitted, do not retry: stop and reply 'noop'."
            ),
            tool_call_id=str(request.tool_call.get("id") or "missing_tool_call_id"),
            name=tool_name,
            status="error",
        )

    @override
    def wrap_tool_call(self, request, handler):
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return self._error_message(request, exc)

    @override
    async def awrap_tool_call(self, request, handler):
        # The review runs through ``ainvoke``, so the async hook is the one that
        # actually fires; the sync version above is kept for symmetry.
        try:
            result = handler(request)
            if inspect.isawaitable(result):
                result = await result
            return result
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return self._error_message(request, exc)


def resolve_review_model_name(review_model_name: str | None) -> str | None:
    """Pick the model the reviewer should use.

    Falls back to the *foreground* default rather than ``config.models[0]``.
    Those two differ in this repo, and the first configured model may not even
    have working credentials — which would fail every review at its first model
    call while the foreground agent keeps working fine.
    """
    if review_model_name:
        return review_model_name
    try:
        from agents.lead_agent.agent import _resolve_model_name

        return _resolve_model_name()
    except Exception:
        logger.warning(
            "Could not resolve the foreground default model for review; falling back to the model factory default.",
            exc_info=True,
        )
        return None


def _build_review_agent(request: ReviewRequest, tools: list[Any]):
    from langchain.agents import create_agent
    from models import create_chat_model

    model_name = resolve_review_model_name(request.review_model_name)
    logger.info("Background review %s using model %s", request.review_thread_id, model_name)
    return create_agent(
        create_chat_model(name=model_name, thinking_enabled=False),
        tools,
        system_prompt=REVIEW_SYSTEM_PROMPT.format(max_actions=request.max_actions),
        # Only tool-error recovery. The reviewer must not inherit memory,
        # summarisation, prompt-cache rotation or todo state from the foreground
        # agent, and no checkpointer means it persists nothing.
        middleware=(_ReviewToolErrorMiddleware(),),
        checkpointer=None,
        name="background-review",
    )


async def run_review(request: ReviewRequest) -> ReviewResult:
    """Run one isolated review. Never raises (§3.4)."""
    applied: list[str] = []
    started_at = perf_counter()
    model_name = resolve_review_model_name(request.review_model_name)
    emit_event(
        "review_started",
        status="running",
        review_thread_id=request.review_thread_id,
        parent_thread_id=request.parent_thread_id,
        agent_name=request.agent_name,
        model_name=model_name,
        max_actions=request.max_actions,
    )

    def on_applied(result: str) -> None:
        applied.append(result)

    try:
        tools = build_review_tools(request, on_applied)
        agent = _build_review_agent(request, tools)
        recursion_limit = max(_MIN_RECURSION_LIMIT, request.max_actions * _RECURSION_LIMIT_PER_ACTION)
        response = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [HumanMessage(content=f"Conversation transcript to review:\n\n{request.transcript}")]},
                config={
                    "configurable": {"thread_id": request.review_thread_id},
                    "recursion_limit": recursion_limit,
                },
            ),
            timeout=request.timeout_seconds,
        )
    except TimeoutError:
        # Writes already applied before the deadline stay applied; each one is
        # individually validated and journalled, so a partial run is safe.
        logger.warning(
            "Background review %s timed out after %ss (%d action(s) applied)",
            request.review_thread_id,
            request.timeout_seconds,
            len(applied),
        )
        result = ReviewResult(
            status="timeout",
            review_thread_id=request.review_thread_id,
            applied=applied,
            error=f"timed out after {request.timeout_seconds}s",
        )
        emit_event(
            "review_failed",
            status="timeout",
            review_thread_id=request.review_thread_id,
            parent_thread_id=request.parent_thread_id,
            reason="timeout",
            applied=len(applied),
            agent_name=request.agent_name,
            model_name=model_name,
            elapsed_ms=round((perf_counter() - started_at) * 1000),
        )
        return result
    except Exception as exc:
        logger.exception("Background review %s failed", request.review_thread_id)
        emit_event(
            "review_failed",
            review_thread_id=request.review_thread_id,
            parent_thread_id=request.parent_thread_id,
            reason=type(exc).__name__,
            applied=len(applied),
            status="failed",
            agent_name=request.agent_name,
            model_name=model_name,
            elapsed_ms=round((perf_counter() - started_at) * 1000),
        )
        return ReviewResult(
            status="failed",
            review_thread_id=request.review_thread_id,
            applied=applied,
            error=str(exc),
        )

    # Only the final text crosses the boundary; the reviewer's internal
    # messages are dropped here and never returned to the caller (§6.4).
    summary = _final_text(response)
    emit_event(
        "review_completed",
        review_thread_id=request.review_thread_id,
        parent_thread_id=request.parent_thread_id,
        applied=len(applied),
        status="completed",
        agent_name=request.agent_name,
        model_name=model_name,
        elapsed_ms=round((perf_counter() - started_at) * 1000),
    )
    return ReviewResult(
        status="completed",
        review_thread_id=request.review_thread_id,
        applied=applied,
        summary=summary,
    )


def _final_text(response: Any) -> str:
    messages = (response or {}).get("messages") if isinstance(response, dict) else None
    if not messages:
        return ""
    content = getattr(messages[-1], "content", "")
    if isinstance(content, list):
        parts = [
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in content
            if isinstance(item, (str, dict))
        ]
        return " ".join(part for part in parts if part).strip()
    return str(content).strip()


class ReviewScheduler:
    """Starts reviews without blocking the foreground turn.

    ``asyncio.run`` may only be called when no loop is running, so the entry
    point checks for a live loop and picks the safe launch strategy (§2.5).
    A bounded semaphore caps concurrency across all threads (§6.5).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._semaphore: threading.BoundedSemaphore | None = None
        self._semaphore_size = 0
        self._threads: set[threading.Thread] = set()

    def _get_semaphore(self, max_concurrent: int) -> threading.BoundedSemaphore:
        with self._lock:
            if self._semaphore is None or self._semaphore_size != max_concurrent:
                self._semaphore = threading.BoundedSemaphore(max_concurrent)
                self._semaphore_size = max_concurrent
            return self._semaphore

    def submit(
        self,
        request: ReviewRequest,
        *,
        max_concurrent: int,
        on_done: Callable[[ReviewResult | None], None] | None = None,
    ) -> bool:
        """Launch *request* in the background. Returns False if slots are full."""
        semaphore = self._get_semaphore(max_concurrent)
        if not semaphore.acquire(blocking=False):
            logger.debug("Background review skipped: %d concurrent review(s) already running", max_concurrent)
            if on_done is not None:
                on_done(None)
            return False

        def worker() -> None:
            result: ReviewResult | None = None
            try:
                result = asyncio.run(run_review(request))
            except Exception:
                logger.exception("Background review thread crashed")
            finally:
                semaphore.release()
                with self._lock:
                    self._threads.discard(threading.current_thread())
                if on_done is not None:
                    try:
                        on_done(result)
                    except Exception:
                        logger.exception("Background review completion callback failed")

        thread = threading.Thread(
            target=worker,
            name=f"background-review-{request.review_thread_id}",
            daemon=True,
        )
        with self._lock:
            self._threads.add(thread)
        thread.start()
        return True

    def wait_for_idle(self, timeout: float = 30.0) -> bool:
        """Join outstanding review threads, for shutdown (§6.5 bounded wait)."""
        with self._lock:
            threads = list(self._threads)
        deadline_per_thread = timeout / max(1, len(threads)) if threads else timeout
        for thread in threads:
            thread.join(timeout=deadline_per_thread)
        with self._lock:
            return not any(thread.is_alive() for thread in self._threads)


_scheduler = ReviewScheduler()


def get_review_scheduler() -> ReviewScheduler:
    return _scheduler
