from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from config import get_paths


class ObservabilityStore:
    """SQLite-backed local observability store."""

    def __init__(self, base_dir: Path | None = None) -> None:
        root = Path(base_dir).resolve() if base_dir is not None else get_paths().base_dir
        self._root = root
        self._db_path = root / "observability.db"
        self._lock = threading.Lock()
        self._ensure_parent_dirs()
        self._init_db()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def observability_dir(self) -> Path:
        return self.root / "observability"

    @property
    def traces_dir(self) -> Path:
        return self.observability_dir / "traces"

    @property
    def threads_dir(self) -> Path:
        return self.observability_dir / "threads"

    @property
    def trajectory_success_path(self) -> Path:
        return self.observability_dir / "trajectory_samples.jsonl"

    @property
    def trajectory_failed_path(self) -> Path:
        return self.observability_dir / "failed_trajectories.jsonl"

    def _ensure_parent_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.observability_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.threads_dir.mkdir(parents=True, exist_ok=True)

    def trace_file_path(self, *, thread_id: str, trace_id: str, started_at: str) -> Path:
        date_part = started_at[:10] if len(started_at) >= 10 else "unknown-date"
        time_part = started_at[11:19].replace(":", "-") if len(started_at) >= 19 else "unknown-time"
        short_trace_id = trace_id.split("-")[0]
        path = self.traces_dir / date_part / thread_id
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{time_part}-{short_trace_id}.json"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    agent_name TEXT,
                    model_name TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    elapsed_ms INTEGER,
                    content_mode TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    failure_reason TEXT,
                    user_input_preview TEXT,
                    user_input_hash TEXT,
                    assistant_output_preview TEXT,
                    assistant_output_hash TEXT,
                    usage_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    span_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    elapsed_ms INTEGER,
                    status TEXT,
                    error_type TEXT,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
                );

                CREATE TABLE IF NOT EXISTS model_calls (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    model_name TEXT,
                    usage_json TEXT NOT NULL,
                    preview_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    elapsed_ms INTEGER,
                    FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    tool_call_id TEXT,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_type TEXT,
                    elapsed_ms INTEGER,
                    args_preview TEXT,
                    args_hash TEXT,
                    result_preview TEXT,
                    result_hash TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
                );

                CREATE TABLE IF NOT EXISTS cache_events (
                    event_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    cache_name TEXT NOT NULL,
                    hit_tokens INTEGER NOT NULL DEFAULT 0,
                    miss_tokens INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
                );

                CREATE TABLE IF NOT EXISTS thread_totals (
                    thread_id TEXT PRIMARY KEY,
                    totals_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trajectory_exports (
                    trace_id TEXT PRIMARY KEY,
                    export_target TEXT NOT NULL,
                    completed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
                );

                CREATE TABLE IF NOT EXISTS legacy_imports (
                    source_path TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def upsert_trace(self, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO traces (
                    trace_id, thread_id, agent_name, model_name, started_at, ended_at, elapsed_ms,
                    content_mode, completed, failure_reason, user_input_preview, user_input_hash,
                    assistant_output_preview, assistant_output_hash, usage_json, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    thread_id=excluded.thread_id,
                    agent_name=excluded.agent_name,
                    model_name=excluded.model_name,
                    started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    elapsed_ms=excluded.elapsed_ms,
                    content_mode=excluded.content_mode,
                    completed=excluded.completed,
                    failure_reason=excluded.failure_reason,
                    user_input_preview=excluded.user_input_preview,
                    user_input_hash=excluded.user_input_hash,
                    assistant_output_preview=excluded.assistant_output_preview,
                    assistant_output_hash=excluded.assistant_output_hash,
                    usage_json=excluded.usage_json,
                    summary_json=excluded.summary_json
                """,
                (
                    payload["trace_id"],
                    payload["thread_id"],
                    payload.get("agent_name"),
                    payload.get("model_name"),
                    payload["started_at"],
                    payload.get("ended_at"),
                    payload.get("elapsed_ms"),
                    payload["content_mode"],
                    1 if payload.get("completed") else 0,
                    payload.get("failure_reason"),
                    payload.get("user_input_preview"),
                    payload.get("user_input_hash"),
                    payload.get("assistant_output_preview"),
                    payload.get("assistant_output_hash"),
                    json.dumps(payload.get("usage", {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("summary", {}), ensure_ascii=False, sort_keys=True),
                ),
            )

    def insert_span(self, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO spans (
                    span_id, trace_id, span_type, name, started_at, ended_at, elapsed_ms,
                    status, error_type, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["span_id"],
                    payload["trace_id"],
                    payload["span_type"],
                    payload["name"],
                    payload["started_at"],
                    payload.get("ended_at"),
                    payload.get("elapsed_ms"),
                    payload.get("status"),
                    payload.get("error_type"),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )

    def insert_model_call(self, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO model_calls (
                    span_id, trace_id, model_name, usage_json, preview_json,
                    started_at, ended_at, elapsed_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["span_id"],
                    payload["trace_id"],
                    payload.get("model_name"),
                    json.dumps(payload.get("usage", {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(payload.get("preview", {}), ensure_ascii=False, sort_keys=True),
                    payload["started_at"],
                    payload.get("ended_at"),
                    payload.get("elapsed_ms"),
                ),
            )

    def insert_tool_call(self, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tool_calls (
                    span_id, trace_id, tool_call_id, tool_name, status, error_type, elapsed_ms,
                    args_preview, args_hash, result_preview, result_hash, retry_count, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["span_id"],
                    payload["trace_id"],
                    payload.get("tool_call_id"),
                    payload["tool_name"],
                    payload.get("status", "ok"),
                    payload.get("error_type"),
                    payload.get("elapsed_ms"),
                    payload.get("args_preview"),
                    payload.get("args_hash"),
                    payload.get("result_preview"),
                    payload.get("result_hash"),
                    payload.get("retry_count", 0),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )

    def insert_cache_event(self, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache_events (
                    event_id, trace_id, cache_name, hit_tokens, miss_tokens, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_id"],
                    payload["trace_id"],
                    payload["cache_name"],
                    payload.get("hit_tokens", 0),
                    payload.get("miss_tokens", 0),
                    json.dumps(payload.get("metadata", {}), ensure_ascii=False, sort_keys=True),
                    payload["created_at"],
                ),
            )

    def upsert_thread_totals(self, thread_id: str, totals: dict[str, Any], metadata: dict[str, Any], updated_at: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO thread_totals (thread_id, totals_json, metadata_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    totals_json=excluded.totals_json,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    thread_id,
                    json.dumps(totals, ensure_ascii=False, sort_keys=True),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    updated_at,
                ),
            )

    def get_thread_totals(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT totals_json, metadata_json, updated_at FROM thread_totals WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "totals": json.loads(row["totals_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "updated_at": row["updated_at"],
        }

    def list_traces_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT trace_id, started_at, ended_at, elapsed_ms, content_mode, completed,
                       failure_reason, user_input_preview, assistant_output_preview, usage_json, summary_json
                FROM traces
                WHERE thread_id = ?
                ORDER BY started_at ASC
                """,
                (thread_id,),
            ).fetchall()
        return [
            {
                "trace_id": row["trace_id"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "elapsed_ms": row["elapsed_ms"],
                "content_mode": row["content_mode"],
                "completed": bool(row["completed"]),
                "failure_reason": row["failure_reason"],
                "user_input_preview": row["user_input_preview"],
                "assistant_output_preview": row["assistant_output_preview"],
                "usage": json.loads(row["usage_json"]),
                "summary": json.loads(row["summary_json"]),
            }
            for row in rows
        ]

    def list_tool_calls_for_trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM tool_calls
                WHERE trace_id = ?
                ORDER BY elapsed_ms DESC, span_id ASC
                """,
                (trace_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def list_model_calls_for_trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT span_id, model_name, usage_json, preview_json, started_at, ended_at, elapsed_ms
                FROM model_calls
                WHERE trace_id = ?
                ORDER BY started_at ASC
                """,
                (trace_id,),
            ).fetchall()
        return [
            {
                "span_id": row["span_id"],
                "model_name": row["model_name"],
                "usage": json.loads(row["usage_json"]),
                "preview": json.loads(row["preview_json"]),
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "elapsed_ms": row["elapsed_ms"],
            }
            for row in rows
        ]

    def list_cache_events_for_trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, cache_name, hit_tokens, miss_tokens, metadata_json, created_at
                FROM cache_events
                WHERE trace_id = ?
                ORDER BY created_at ASC
                """,
                (trace_id,),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "cache_name": row["cache_name"],
                "hit_tokens": row["hit_tokens"],
                "miss_tokens": row["miss_tokens"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def insert_trajectory_export(self, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trajectory_exports (
                    trace_id, export_target, completed, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payload["trace_id"],
                    payload["export_target"],
                    1 if payload.get("completed") else 0,
                    payload["created_at"],
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )

    def has_legacy_import(self, source_path: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM legacy_imports WHERE source_path = ?",
                (source_path,),
            ).fetchone()
        return row is not None

    def record_legacy_import(self, source_path: str, thread_id: str, imported_at: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO legacy_imports (source_path, thread_id, imported_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    source_path,
                    thread_id,
                    imported_at,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )

    def list_legacy_imports_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_path, imported_at, payload_json
                FROM legacy_imports
                WHERE thread_id = ?
                ORDER BY source_path ASC
                """,
                (thread_id,),
            ).fetchall()
        return [
            {
                "source_path": row["source_path"],
                "imported_at": row["imported_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
