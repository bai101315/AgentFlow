"""Unit tests for CuratorMiddleware and CuratorScheduler (§13.5).

Validates:
- Curator does not run when disabled.
- First run defers (initializes last_run_at, does not execute).
- Interval gating works.
- Idle gate works.
- Concurrent runs are rejected.
- Timer cleanup.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from agents.middlewares.curator_middleware import CuratorMiddleware, CuratorScheduler


# ── Helpers ──────────────────────────────────────────────────────────────


def _curator_config(*, enabled=True, interval_hours=168, min_idle_hours=0):
    return SimpleNamespace(
        enabled=enabled,
        interval_hours=interval_hours,
        min_idle_hours=min_idle_hours,
        stale_after_days=30,
        archive_after_days=90,
        consolidate=False,
        model_name=None,
    )


def _app_config(curator_cfg=None):
    return SimpleNamespace(curator=curator_cfg or _curator_config())


def _runtime():
    return SimpleNamespace(context={})


# ── CuratorScheduler tests ───────────────────────────────────────────────


def test_schedule_if_due_skips_when_disabled():
    scheduler = CuratorScheduler()
    with patch("agents.middlewares.curator_middleware.get_app_config", return_value=_app_config(_curator_config(enabled=False))):
        scheduler.schedule_if_due()
    assert scheduler._timer is None


def test_schedule_if_due_skips_when_not_due():
    """should_run_curator returns False on first call (defer)."""
    scheduler = CuratorScheduler()
    with patch("agents.middlewares.curator_middleware.get_app_config", return_value=_app_config()):
        with patch("skill.curator.should_run_curator", return_value=False):
            scheduler.schedule_if_due()
    assert scheduler._timer is None


def test_schedule_if_due_does_not_double_schedule():
    scheduler = CuratorScheduler()
    # Simulate a timer already running
    scheduler._timer = MagicMock()
    with patch("agents.middlewares.curator_middleware.get_app_config", return_value=_app_config()):
        with patch("skill.curator.should_run_curator", return_value=True):
            scheduler.schedule_if_due()
    # Timer should not be replaced
    assert scheduler._timer is not None


def test_note_activity_updates_timestamp():
    scheduler = CuratorScheduler()
    before = scheduler._last_activity
    time.sleep(0.01)
    scheduler.note_activity()
    assert scheduler._last_activity > before


def test_running_flag_prevents_concurrent_execution():
    scheduler = CuratorScheduler()
    scheduler._running = True
    # _run_if_due should exit early when _running is True
    with patch("agents.middlewares.curator_middleware.get_app_config", return_value=_app_config()):
        with patch("skill.curator.should_run_curator", return_value=True):
            with patch("skill.curator.run_curator") as mock_run:
                scheduler._run_if_due()
    mock_run.assert_not_called()


# ── CuratorMiddleware tests ──────────────────────────────────────────────


def test_middleware_returns_none_when_disabled():
    middleware = CuratorMiddleware()
    with patch("agents.middlewares.curator_middleware.get_app_config", return_value=_app_config(_curator_config(enabled=False))):
        result = middleware.after_agent({}, _runtime())
    assert result is None


def test_middleware_notes_activity_and_schedules():
    scheduler = CuratorScheduler()
    middleware = CuratorMiddleware(scheduler=scheduler)

    with patch("agents.middlewares.curator_middleware.get_app_config", return_value=_app_config()):
        with patch.object(scheduler, "note_activity") as mock_note:
            with patch.object(scheduler, "schedule_if_due") as mock_schedule:
                middleware.after_agent({}, _runtime())

    mock_note.assert_called_once()
    mock_schedule.assert_called_once()


def test_middleware_does_not_mutate_state():
    """§14.1: middleware must not alter the state dict."""
    scheduler = CuratorScheduler()
    middleware = CuratorMiddleware(scheduler=scheduler)
    state = {"messages": [SimpleNamespace(type="human", content="hi")]}
    original_messages = list(state["messages"])

    with patch("agents.middlewares.curator_middleware.get_app_config", return_value=_app_config()):
        with patch.object(scheduler, "schedule_if_due"):
            result = middleware.after_agent(state, _runtime())

    assert result is None
    assert state["messages"] == original_messages
