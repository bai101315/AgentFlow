"""Middleware scheduler for skill curator lifecycle runs."""

from __future__ import annotations

import logging
import threading
import time
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from config import get_app_config
from skill.curator import run_curator, should_run_curator

logger = logging.getLogger(__name__)


class CuratorScheduler:
    """Idle-aware scheduler for deterministic skill lifecycle maintenance."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_activity = time.time()
        self._timer: threading.Timer | None = None
        self._running = False

    def note_activity(self) -> None:
        with self._lock:
            self._last_activity = time.time()

    def _idle_for_seconds(self) -> float:
        with self._lock:
            return max(0.0, time.time() - self._last_activity)

    def _run_if_due(self) -> None:
        config = get_app_config().curator
        idle = self._idle_for_seconds()
        try:
            if should_run_curator(config, idle_for_seconds=idle):
                with self._lock:
                    if self._running:
                        return
                    self._running = True
                try:
                    run_curator(config)
                finally:
                    with self._lock:
                        self._running = False
        except Exception:
            logger.exception("Curator run failed")
        finally:
            with self._lock:
                self._timer = None

    def schedule_if_due(self) -> None:
        config = get_app_config().curator
        if not config.enabled:
            return
        if not should_run_curator(config, idle_for_seconds=None):
            return

        min_idle_seconds = config.min_idle_hours * 3600
        delay = max(0.0, min_idle_seconds - self._idle_for_seconds())
        with self._lock:
            if self._timer is not None or self._running:
                return
            self._timer = threading.Timer(delay, self._run_if_due)
            self._timer.daemon = True
            self._timer.name = "skill-curator"
            self._timer.start()


_scheduler = CuratorScheduler()


class CuratorMiddleware(AgentMiddleware[AgentState]):
    """Mark activity and schedule curator checks after agent turns."""

    state_schema = AgentState

    def __init__(self, scheduler: CuratorScheduler | None = None):
        super().__init__()
        self._scheduler = scheduler or _scheduler

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        config = get_app_config()
        if not getattr(config.curator, "enabled", False):
            return None
        self._scheduler.note_activity()
        self._scheduler.schedule_if_due()
        return None
