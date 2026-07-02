from __future__ import annotations
import json

from langchain_core.messages import AIMessage, HumanMessage

from session_search.store import SessionSearchStore



# 检查会话搜索能不能索引、搜索、去重
def test_session_search_indexes_dedupes_searches_and_scrolls(tmp_path):
    store = SessionSearchStore(tmp_path / "sessions.db")
    messages = [
        HumanMessage(content="Alpha project chose SQLite FTS5 for recall.", id="h1"),
        AIMessage(content="Recorded the decision.", id="a1"),
    ]


    assert store.index_messages(thread_id="thread-a", messages=messages) == 2
    assert store.index_messages(thread_id="thread-a", messages=messages) == 0

    hits = store.search("Alpha SQLite recall")
    assert hits
    assert hits[0]["session_id"] == "thread-a"

    around = store.around(session_id="thread-a", around_message_id="h1")
    assert [message["role"] for message in around] == ["user", "assistant"]

    recent = store.recent_sessions()
    assert recent[0]["session_id"] == "thread-a"
