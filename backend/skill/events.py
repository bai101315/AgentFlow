"""Append-only event log for automated self-improvement changes.

Every automated skill change must be traceable back to the thread and agent
that caused it (docs/SELF_IMPROVING_MIGRATION.md §3.3).  Events land in
``{AGENTFLOW_HOME}/self_improvement/events.jsonl``.

Writing an event must never break the action it describes, so
``emit_event`` swallows and logs its own failures (§3.4).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from config.paths import get_paths

logger = logging.getLogger(__name__)

EVENTS_FILE_NAME = "events.jsonl"

_write_lock = threading.Lock()
_listeners_lock = threading.RLock()
_listeners: set[Callable[[dict[str, Any]], None]] = set()


def get_self_improvement_dir() -> Path:
    return get_paths().base_dir / "self_improvement"


def get_events_file() -> Path:
    return get_self_improvement_dir() / EVENTS_FILE_NAME


def subscribe(listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
    """Subscribe to events emitted in this process.

    Listeners are notification-only: failures are isolated in ``emit_event``
    and can never affect the operation that produced the event.
    """
    with _listeners_lock:
        _listeners.add(listener)

    def unsubscribe() -> None:
        with _listeners_lock:
            _listeners.discard(listener)

    return unsubscribe


def emit_event(event: str, **fields: Any) -> None:
    """Append one self-improvement event. Never raises."""
    event_id = str(uuid.uuid4())
    allowed = {
        key: value for key, value in fields.items()
        if key in {
            "status", "thread_id", "parent_thread_id", "review_thread_id", "agent_name",
            "skill", "action", "elapsed_ms", "model_name", "reason", "applied", "max_actions",
            "signal_type", "origin", "execution_context", "scanner", "count",
            "file_path", "created", "updated", "deleted",
            "input_tokens", "output_tokens", "total_tokens",
            "pruned",
            "trigger",
        }
    }
    record = {
        "event_id": event_id,
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **allowed,
    }
    try:
        path = get_events_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record, ensure_ascii=False, default=str)
        with _write_lock, path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    except Exception:
        logger.warning("Failed to write self-improvement event %s to JSONL", event, exc_info=True)

    try:
        from observability.store import ObservabilityStore

        ObservabilityStore().insert_background_event({
            "event_id": event_id,
            "created_at": record["ts"],
            "event_type": event,
            "status": record.get("status"),
            "thread_id": record.get("thread_id"),
            "parent_thread_id": record.get("parent_thread_id"),
            "review_thread_id": record.get("review_thread_id"),
            "agent_name": record.get("agent_name"),
            "skill": record.get("skill"),
            "action": record.get("action"),
            "elapsed_ms": record.get("elapsed_ms"),
            "model_name": record.get("model_name"),
            "metadata": {k: v for k, v in record.items() if k not in {
                "event_id", "ts", "event", "status", "thread_id", "parent_thread_id",
                "review_thread_id", "agent_name", "skill", "action", "elapsed_ms", "model_name",
            }},
        })
    except Exception:
        logger.warning("Failed to write self-improvement event %s to observability DB", event, exc_info=True)

    with _listeners_lock:
        listeners = tuple(_listeners)
    for listener in listeners:
        try:
            listener(dict(record))
        except Exception:
            logger.warning("Self-improvement event listener failed for %s", event, exc_info=True)


def read_events(limit: int | None = None) -> list[dict[str, Any]]:
    """Read events oldest-first, optionally keeping only the last *limit*."""
    path = get_events_file()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if limit is not None:
        return records[-limit:]
    return records
