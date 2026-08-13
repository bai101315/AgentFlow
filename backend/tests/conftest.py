from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def _reset_app_config_after_test():
    try:
        from config.app_config import reset_app_config
    except Exception:
        yield
        return

    yield
    reset_app_config()


@pytest.fixture
def real_events():
    """Opt out of the autouse event-log isolation.

    Tests that request this fixture assert on real event persistence (via
    ``skill.events.read_events``) and are responsible for pointing the paths
    at an isolated directory themselves (e.g. through tmp_path).
    """
    return None


@pytest.fixture(autouse=True)
def _isolate_self_improvement_events(monkeypatch, request):
    """Keep self-improvement events out of the real event log.

    ``emit_event`` appends to ``{AGENTFLOW_HOME}/self_improvement/events.jsonl``
    and the observability DB.  Tests that exercise drop paths (review_dropped,
    memory_dropped) or real skill writes would otherwise pollute the user's
    production event stream with ``thread-1``-style test events — exactly the
    phantom "dropped" entries seen in the observability UI.  Swallow the events
    in tests; none of the current tests assert on event persistence.
    """
    if "real_events" in request.fixturenames:
        # The test isolates its own event paths and asserts on persistence.
        yield None
        return

    import skill.events as events_module

    emitted: list[dict] = []

    def _noop_emit(event: str, **fields) -> None:
        emitted.append({"event": event, **fields})

    monkeypatch.setattr(events_module, "emit_event", _noop_emit)
    # Module-level `from skill.events import emit_event` bindings are resolved
    # at import time, so patch those namespaces too.
    import agents.review_agent.runtime as review_runtime
    import tools.skill_manage_tool as skill_manage_tool

    monkeypatch.setattr(review_runtime, "emit_event", _noop_emit)
    monkeypatch.setattr(skill_manage_tool, "emit_event", _noop_emit)
    yield emitted


@pytest.fixture
def tmp_path():
    """Project-local temporary directory.

    The default pytest tmp_path uses the OS temp directory. On some Windows
    setups that directory is locked down, so the beginner test suite keeps
    temporary files inside the writable project tree.
    """
    root = BACKEND_ROOT / ".test-tmp"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
