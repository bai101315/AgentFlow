from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from config.app_config import set_app_config
from session_search.store import SessionSearchStore
from tools.builtins.session_search_tool import session_search_tool


def _messages() -> list:
    return [
        HumanMessage(content="Alpha project chose SQLite FTS5 for recall.", id="h1"),
        AIMessage(content="Recorded the decision.", id="a1"),
    ]


def _tool_result(**kwargs):
    raw = session_search_tool.invoke(kwargs)
    return json.loads(raw)


def test_session_search_indexes_dedupes_searches_and_scrolls(tmp_path):
    store = SessionSearchStore(tmp_path / "sessions.db")

    assert store.index_messages(thread_id="thread-a", messages=_messages()) == 2
    assert store.index_messages(thread_id="thread-a", messages=_messages()) == 0

    hits = store.search("Alpha SQLite recall")
    assert hits
    assert hits[0]["session_id"] == "thread-a"
    assert "snippet" in hits[0]
    assert "content" not in hits[0]

    around = store.around(session_id="thread-a", around_message_id="h1")
    assert [message["role"] for message in around["messages"]] == ["user", "assistant"]

    recent = store.recent_sessions()
    assert recent[0]["session_id"] == "thread-a"


def test_session_search_skips_tool_call_ai_messages(tmp_path):
    store = SessionSearchStore(tmp_path / "sessions.db")
    messages = [
        HumanMessage(content="Please inspect the deployment logs.", id="h1"),
        AIMessage(
            content="I will call a tool now.",
            id="a-tool",
            tool_calls=[{"name": "read_file", "args": {"path": "logs.txt"}, "id": "call-1"}],
        ),
        AIMessage(content="The final answer mentions the deploy fix.", id="a-final"),
    ]

    assert store.index_messages(thread_id="thread-a", messages=messages) == 2
    assert store.search("deployment logs")
    assert not store.search("call a tool now")
    assert store.search("deploy fix")


def test_session_search_read_session_head_tail(tmp_path):
    store = SessionSearchStore(tmp_path / "sessions.db")
    messages = [HumanMessage(content=f"message {i}", id=f"h{i}") for i in range(40)]
    store.index_messages(thread_id="thread-long", messages=messages)

    result = store.read_session("thread-long", head=3, tail=2)

    assert result["message_count"] == 40
    assert result["omitted_count"] == 35
    assert [message["message_id"] for message in result["messages"]] == ["h0", "h1", "h2", "h38", "h39"]


def test_session_search_delete_session(tmp_path):
    store = SessionSearchStore(tmp_path / "sessions.db")
    store.index_messages(thread_id="thread-delete", messages=[HumanMessage(content="delete me marker", id="h1")])

    assert store.search("delete marker")
    assert store.delete_session("thread-delete") == 1
    assert not store.search("delete marker")


def test_session_search_prune_older_than(tmp_path):
    db_path = tmp_path / "sessions.db"
    store = SessionSearchStore(db_path)
    store.index_messages(thread_id="thread-old", messages=[HumanMessage(content="old prune marker", id="h1")])

    old_ts = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE session_messages SET ts = ?", (old_ts,))

    assert store.prune_older_than(1) == 1
    assert not store.search("old prune marker")


def test_session_search_like_fallback_for_chinese(tmp_path):
    store = SessionSearchStore(tmp_path / "sessions.db")
    content = "\u4e0a\u6b21\u51b3\u5b9a\u4f7f\u7528\u84dd\u8272\u6309\u94ae\u4f5c\u4e3a\u4e3b\u64cd\u4f5c\u3002"
    query = "\u84dd\u8272\u6309\u94ae"
    store.index_messages(thread_id="thread-cn", messages=[HumanMessage(content=content, id="h1")])

    hits = store.search(query)

    assert hits
    assert hits[0]["session_id"] == "thread-cn"


def test_session_search_tool_modes(tmp_path):
    db_path = tmp_path / "sessions.db"
    store = SessionSearchStore(db_path)
    store.index_messages(thread_id="thread-tool", messages=_messages())
    set_app_config(
        SimpleNamespace(
            session_search=SimpleNamespace(enabled=True, db_path=str(db_path), max_results=5),
        )
    )

    query_result = _tool_result(query="Alpha SQLite")
    assert query_result["mode"] == "query"
    assert query_result["results"]

    read_result = _tool_result(session_id="thread-tool")
    assert read_result["mode"] == "read"
    assert read_result["messages"]

    around_result = _tool_result(session_id="thread-tool", around_message_id="h1", window=1)
    assert around_result["mode"] == "around"
    assert [message["role"] for message in around_result["messages"]] == ["user", "assistant"]

    recent_result = _tool_result()
    assert recent_result["mode"] == "recent"
    assert recent_result["sessions"]
