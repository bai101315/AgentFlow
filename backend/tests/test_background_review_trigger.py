from __future__ import annotations

from types import SimpleNamespace

from agents.middlewares.background_review_middleware import BackgroundReviewMiddleware, build_transcript
from agents.review_agent.runtime import ReviewRequest
from config.app_config import set_app_config


class _FakeScheduler:
    def __init__(self, *, accept: bool = True):
        self.requests: list[ReviewRequest] = []
        self.accept = accept

    def submit(self, request, *, max_concurrent, on_done=None):
        self.requests.append(request)
        if not self.accept and on_done is not None:
            on_done(None)
        return self.accept


def _configure(*, enabled=True, nudge=3, max_concurrent=1):
    set_app_config(
        SimpleNamespace(
            skill_evolution=SimpleNamespace(enabled=True),
            background_review=SimpleNamespace(
                enabled=enabled,
                skill_nudge_interval=nudge,
                max_messages=24,
                max_actions_per_review=3,
                timeout_seconds=120,
                max_concurrent_reviews=max_concurrent,
                review_model_name=None,
                notifications="off",
            ),
        )
    )


def _runtime(thread_id="thread-1"):
    return SimpleNamespace(context={"thread_id": thread_id})


def _human(text="do the thing"):
    return SimpleNamespace(type="human", content=text, tool_calls=None, additional_kwargs={})


def _ai_with_calls(*names, start=0):
    return SimpleNamespace(
        type="ai",
        content="",
        tool_calls=[{"id": f"call-{start + i}", "name": name, "args": {}} for i, name in enumerate(names)],
        additional_kwargs={},
    )


def _ai(text="done"):
    return SimpleNamespace(type="ai", content=text, tool_calls=None, additional_kwargs={})


def test_no_review_below_threshold():
    _configure(nudge=5)
    scheduler = _FakeScheduler()
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    state = {"messages": [_human(), _ai_with_calls("read_file", "web_search"), _ai()]}
    middleware.after_agent(state, _runtime())

    assert scheduler.requests == []


def test_review_triggers_once_at_threshold():
    _configure(nudge=2)
    scheduler = _FakeScheduler()
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    state = {"messages": [_human(), _ai_with_calls("read_file", "web_search"), _ai()]}
    middleware.after_agent(state, _runtime())
    # A second pass over the same messages sees no *new* tool calls.
    middleware.after_agent(state, _runtime())

    assert len(scheduler.requests) == 1


def test_skill_manage_call_resets_the_counter():
    """The nudge exists to prompt a skill write; an actual write cancels it."""
    _configure(nudge=2)
    scheduler = _FakeScheduler()
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    state = {"messages": [_human(), _ai_with_calls("read_file", "skill_manage"), _ai()]}
    middleware.after_agent(state, _runtime())

    assert scheduler.requests == []
    assert middleware._states["thread-1"].tool_calls_since_skill == 0


def test_concurrent_review_for_same_thread_is_dropped():
    _configure(nudge=1)
    scheduler = _FakeScheduler()
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    middleware.after_agent({"messages": [_human(), _ai_with_calls("read_file", start=0), _ai()]}, _runtime())
    # review_running is still set because the fake scheduler never calls on_done.
    middleware.after_agent({"messages": [_human(), _ai_with_calls("web_search", start=1), _ai()]}, _runtime())

    assert len(scheduler.requests) == 1


def test_review_running_is_cleared_when_scheduler_refuses():
    """A refused submission must not wedge the thread permanently."""
    _configure(nudge=1)
    scheduler = _FakeScheduler(accept=False)
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    middleware.after_agent({"messages": [_human(), _ai_with_calls("read_file", start=0), _ai()]}, _runtime())

    assert middleware._states["thread-1"].review_running is False


def test_disabled_config_never_triggers():
    _configure(enabled=False, nudge=1)
    scheduler = _FakeScheduler()
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    middleware.after_agent({"messages": [_human(), _ai_with_calls("read_file"), _ai()]}, _runtime())

    assert scheduler.requests == []


def test_after_agent_never_mutates_parent_state():
    """§14.1: the review path must not touch the parent's messages."""
    _configure(nudge=1)
    scheduler = _FakeScheduler()
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    messages = [_human(), _ai_with_calls("read_file"), _ai()]
    state = {"messages": messages}
    result = middleware.after_agent(state, _runtime())

    assert result is None
    assert state["messages"] is messages
    assert len(messages) == 3


def test_request_carries_parent_thread_and_text_snapshot():
    _configure(nudge=1)
    scheduler = _FakeScheduler()
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    middleware.after_agent({"messages": [_human("fix the parser"), _ai_with_calls("read_file"), _ai("fixed")]}, _runtime())

    request = scheduler.requests[0]
    assert request.parent_thread_id == "thread-1"
    assert isinstance(request.transcript, str)
    assert "fix the parser" in request.transcript
    assert request.max_actions == 3


def test_separate_threads_are_counted_independently():
    _configure(nudge=2)
    scheduler = _FakeScheduler()
    middleware = BackgroundReviewMiddleware(scheduler=scheduler)

    middleware.after_agent({"messages": [_human(), _ai_with_calls("read_file", start=0), _ai()]}, _runtime("thread-a"))
    middleware.after_agent({"messages": [_human(), _ai_with_calls("read_file", start=1), _ai()]}, _runtime("thread-b"))

    assert scheduler.requests == []


def test_build_transcript_respects_max_messages():
    messages = [_human(f"turn {i}") for i in range(10)]

    transcript = build_transcript(messages, max_messages=3)

    assert "turn 9" in transcript
    assert "turn 6" not in transcript
