"""Middleware that indexes completed turns for session_search."""

from __future__ import annotations

import logging
import threading
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from config import get_app_config
from session_search.store import index_messages

logger = logging.getLogger(__name__)


class SessionSearchMiddleware(AgentMiddleware[AgentState]):
    """Incrementally index user messages and final assistant replies."""

    state_schema = AgentState

    def __init__(self, *, inline: bool = False):
        super().__init__()
        self._inline = inline

    def _thread_id(self, runtime: Runtime) -> str | None:
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            config_data = get_config()
            thread_id = config_data.get("configurable", {}).get("thread_id")
        return thread_id

    def _index(self, *, thread_id: str, messages: list) -> None:
        try:
            config = get_app_config()
            index_messages(thread_id=thread_id, messages=messages, db_path=config.session_search.db_path)
        except Exception:
            logger.exception("Session search indexing failed")

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        config = get_app_config()
        if not getattr(config.session_search, "enabled", False):
            return None

        thread_id = self._thread_id(runtime)
        messages = state.get("messages", [])
        if not thread_id or not messages:
            return None

        if self._inline:
            self._index(thread_id=thread_id, messages=list(messages))
        else:
            threading.Thread(
                target=self._index,
                kwargs={"thread_id": thread_id, "messages": list(messages)},
                name=f"session-search-index-{thread_id}",
                daemon=True,
            ).start()
        return None
