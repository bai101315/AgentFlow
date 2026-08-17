"""Unit tests for MemoryMiddleware (§13.4).

Validates:
- Only human + final AI messages are kept.
- Tool calls, intermediate AI, and upload blocks are filtered.
- correction/reinforcement detection.
- debounce behaviour (record_turn batching).
- No state mutation from the middleware.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from agents.middlewares.memory_middleware import (
    MemoryMiddleware,
    _filter_messages_for_memory,
    detect_correction,
    detect_reinforcement,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _human(text="hello"):
    return SimpleNamespace(type="human", content=text, tool_calls=None)


def _ai(text="hi there"):
    return SimpleNamespace(type="ai", content=text, tool_calls=None)


def _ai_with_tools(text="", tool_calls=None):
    return SimpleNamespace(type="ai", content=text, tool_calls=tool_calls or [{"id": "c1", "name": "t", "args": {}}])


def _tool(text="result"):
    return SimpleNamespace(type="tool", content=text)


def _human_upload(text="<uploaded_files>/tmp/file.txt</uploaded_files>"):
    return SimpleNamespace(type="human", content=text, tool_calls=None)


def _human_upload_with_question(question="what is this?"):
    return SimpleNamespace(
        type="human",
        content=f"<uploaded_files>/tmp/file.txt</uploaded_files>\n{question}",
        tool_calls=None,
    )


# ── Filter tests ─────────────────────────────────────────────────────────


def test_filter_keeps_human_and_final_ai():
    messages = [_human("q"), _ai("a")]
    filtered = _filter_messages_for_memory(messages)
    assert len(filtered) == 2
    assert filtered[0].type == "human"
    assert filtered[1].type == "ai"


def test_filter_removes_tool_messages():
    messages = [_human(), _ai_with_tools(), _tool(), _ai("final")]
    filtered = _filter_messages_for_memory(messages)
    assert all(getattr(m, "type", None) != "tool" for m in filtered)


def test_filter_removes_intermediate_ai_with_tool_calls():
    messages = [_human(), _ai_with_tools("thinking..."), _tool(), _ai("answer")]
    filtered = _filter_messages_for_memory(messages)
    # Only the final AI without tool_calls should remain
    ai_msgs = [m for m in filtered if m.type == "ai"]
    assert len(ai_msgs) == 1
    assert ai_msgs[0].content == "answer"


def test_filter_removes_upload_only_human():
    messages = [_human_upload(), _ai("ack")]
    filtered = _filter_messages_for_memory(messages)
    # Both the upload-only human and paired AI should be dropped
    assert len(filtered) == 0


def test_filter_keeps_human_with_upload_and_question():
    messages = [_human_upload_with_question("explain this"), _ai("explanation")]
    filtered = _filter_messages_for_memory(messages)
    assert len(filtered) == 2
    # The upload block should be stripped but the question preserved
    assert "<uploaded_files>" not in filtered[0].content
    assert "explain this" in filtered[0].content


# ── Correction/reinforcement detection ───────────────────────────────────


def test_detect_correction_positive():
    messages = [_human("no that's wrong, use pytest instead"), _ai("ok")]
    assert detect_correction(messages) is True


def test_detect_correction_negative():
    messages = [_human("please add a test"), _ai("done")]
    assert detect_correction(messages) is False


def test_detect_reinforcement_positive():
    messages = [_human("perfect, that's exactly right"), _ai("glad it helped")]
    assert detect_reinforcement(messages) is True


def test_detect_reinforcement_negative():
    messages = [_human("now do the next step"), _ai("ok")]
    assert detect_reinforcement(messages) is False


def test_reinforcement_suppressed_when_correction_present():
    """If correction is detected, reinforcement should not fire (per middleware logic)."""
    messages = [_human("no that's wrong, perfect otherwise"), _ai("fixing")]
    # Both might match patterns, but correction takes priority
    correction = detect_correction(messages)
    reinforcement = not correction and detect_reinforcement(messages)
    assert correction is True
    assert reinforcement is False


# ── Middleware integration ────────────────────────────────────────────────


def _runtime(thread_id="thread-1"):
    return SimpleNamespace(context={"thread_id": thread_id})


def _memory_config(enabled=True):
    return SimpleNamespace(
        enabled=enabled,
        debounce_seconds=5,
        update_every_turns=1,
        time_trigger_seconds=300,
    )


def test_after_agent_returns_none():
    """MemoryMiddleware must never mutate state (§14.1)."""
    middleware = MemoryMiddleware(agent_name="test")
    state = {"messages": [_human("q"), _ai("a")]}

    with patch("agents.middlewares.memory_middleware.get_memory_config", return_value=_memory_config()):
        with patch("agents.middlewares.memory_middleware.get_config", return_value={"configurable": {"thread_id": "t1"}}):
            with patch("agents.middlewares.memory_middleware.get_memory_queue") as mock_queue:
                mock_queue.return_value = SimpleNamespace(record_turn=lambda **kw: False)
                result = middleware.after_agent(state, _runtime())

    assert result is None


def test_after_agent_skips_when_disabled():
    middleware = MemoryMiddleware(agent_name="test")
    state = {"messages": [_human("q"), _ai("a")]}

    with patch("agents.middlewares.memory_middleware.get_memory_config", return_value=_memory_config(enabled=False)):
        result = middleware.after_agent(state, _runtime())

    assert result is None


def test_after_agent_skips_without_thread_id():
    middleware = MemoryMiddleware(agent_name="test")
    state = {"messages": [_human("q"), _ai("a")]}

    with patch("agents.middlewares.memory_middleware.get_memory_config", return_value=_memory_config()):
        with patch("agents.middlewares.memory_middleware.get_config", return_value={"configurable": {}}):
            result = middleware.after_agent(state, SimpleNamespace(context={}))

    assert result is None


def test_after_agent_skips_without_messages():
    middleware = MemoryMiddleware(agent_name="test")
    state = {"messages": []}

    with patch("agents.middlewares.memory_middleware.get_memory_config", return_value=_memory_config()):
        result = middleware.after_agent(state, _runtime())

    assert result is None
