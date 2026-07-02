"""Built-in tool for cross-session search."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from config import get_app_config
from session_search.store import search_sessions


@tool("session_search", parse_docstring=True)
def session_search_tool(
    query: str | None = None,
    session_id: str | None = None,
    around_message_id: str | None = None,
    max_results: int | None = None,
) -> str:
    """Search or browse indexed conversation history.

    Args:
        query: Full-text query for past conversation content. Omit to browse recent sessions.
        session_id: Session id to inspect when using around_message_id.
        around_message_id: Message database id or original message id to scroll around within a session.
        max_results: Optional result limit. Defaults to the configured session_search.max_results.
    """
    config = get_app_config()
    search_config = config.session_search
    limit = max_results or search_config.max_results
    result = search_sessions(
        query=query,
        session_id=session_id,
        around_message_id=around_message_id,
        max_results=limit,
        db_path=search_config.db_path,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)
