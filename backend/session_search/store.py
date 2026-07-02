"""SQLite FTS5 storage for cross-session conversation search."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.paths import get_paths, resolve_path

_UPLOAD_BLOCK_RE = re.compile(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _extract_message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return str(content)


def _message_role(message: Any) -> str | None:
    msg_type = getattr(message, "type", None)
    if msg_type == "human":
        return "user"
    if msg_type == "ai":
        return "assistant"
    return None


def _iter_indexable_messages(messages: list[Any]) -> list[tuple[int, str, str, str | None]]:
    indexable: list[tuple[int, str, str, str | None]] = []
    skip_next_ai = False
    for ordinal, msg in enumerate(messages):
        role = _message_role(msg)
        if role == "user":
            content = _extract_message_text(msg)
            if "<uploaded_files>" in content:
                content = _UPLOAD_BLOCK_RE.sub("", content).strip()
                if not content:
                    skip_next_ai = True
                    continue
            if content.strip():
                indexable.append((ordinal, role, content.strip(), getattr(msg, "id", None)))
                skip_next_ai = False
        elif role == "assistant":
            if getattr(msg, "tool_calls", None):
                continue
            if skip_next_ai:
                skip_next_ai = False
                continue
            content = _extract_message_text(msg).strip()
            if content:
                indexable.append((ordinal, role, content, getattr(msg, "id", None)))
    return indexable


def _default_db_path() -> Path:
    return get_paths().base_dir / "session_search.db"


def _resolve_db_path(db_path: str | None) -> Path:
    if not db_path:
        return _default_db_path()
    return resolve_path(db_path)


def _dedupe_key(session_id: str, role: str, ordinal: int, content: str, message_id: str | None) -> str:
    if message_id:
        source = f"{session_id}|{message_id}|{role}"
    else:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source = f"{session_id}|{ordinal}|{role}|{digest}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _format_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "thread_id": row["thread_id"],
        "message_id": row["message_id"],
        "role": row["role"],
        "content": row["content"],
        "ts": row["ts"],
        "ordinal": row["ordinal"],
    }


def _fts_query(query: str) -> str:
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        return ""
    quoted = []
    for token in tokens[:12]:
        safe = token.replace('"', '""')
        quoted.append(f'"{safe}"')
    return " OR ".join(quoted)


class SessionSearchStore:
    """Small synchronous SQLite store guarded by a process-local lock."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = _resolve_db_path(str(db_path) if db_path is not None else None)
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    message_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    metadata TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS session_messages_fts
                USING fts5(content, role, session_id, content='session_messages', content_rowid='id')
                """
            )

    def index_messages(self, *, thread_id: str, messages: list[Any], session_id: str | None = None) -> int:
        session_id = session_id or thread_id
        self.ensure_schema()
        inserted = 0
        now = _utc_now_iso()
        with self._lock, self._connection() as conn:
            for ordinal, role, content, message_id in _iter_indexable_messages(messages):
                key = _dedupe_key(session_id, role, ordinal, content, str(message_id) if message_id else None)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO session_messages
                        (dedupe_key, session_id, thread_id, message_id, role, content, ts, ordinal, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        session_id,
                        thread_id,
                        str(message_id) if message_id else None,
                        role,
                        content,
                        now,
                        ordinal,
                        json.dumps({}, ensure_ascii=False),
                    ),
                )
                if cursor.rowcount:
                    row_id = cursor.lastrowid
                    conn.execute(
                        "INSERT INTO session_messages_fts(rowid, content, role, session_id) VALUES (?, ?, ?, ?)",
                        (row_id, content, role, session_id),
                    )
                    inserted += 1
        return inserted

    def search(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        self.ensure_schema()
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT m.*, bm25(session_messages_fts) AS score
                FROM session_messages_fts
                JOIN session_messages m ON m.id = session_messages_fts.rowid
                WHERE session_messages_fts MATCH ?
                ORDER BY score ASC, m.ts DESC
                LIMIT ?
                """,
                (fts_query, max_results),
            ).fetchall()
        return [_format_row(row) for row in rows]

    def around(self, *, session_id: str, around_message_id: str | int, window: int = 3) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self._lock, self._connection() as conn:
            anchor = conn.execute(
                """
                SELECT * FROM session_messages
                WHERE session_id = ?
                  AND (message_id = ? OR CAST(id AS TEXT) = ?)
                ORDER BY ordinal ASC
                LIMIT 1
                """,
                (session_id, str(around_message_id), str(around_message_id)),
            ).fetchone()
            if anchor is None:
                return []
            start = max(0, int(anchor["ordinal"]) - window)
            end = int(anchor["ordinal"]) + window
            rows = conn.execute(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND ordinal BETWEEN ? AND ?
                ORDER BY ordinal ASC, id ASC
                """,
                (session_id, start, end),
            ).fetchall()
        return [_format_row(row) for row in rows]

    def recent_sessions(self, *, max_results: int = 5) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    session_id,
                    thread_id,
                    MAX(ts) AS last_ts,
                    COUNT(*) AS message_count,
                    (
                        SELECT content
                        FROM session_messages AS first_msg
                        WHERE first_msg.session_id = grouped.session_id
                        ORDER BY ordinal ASC, id ASC
                        LIMIT 1
                    ) AS preview
                FROM session_messages AS grouped
                GROUP BY session_id, thread_id
                ORDER BY last_ts DESC
                LIMIT ?
                """,
                (max_results,),
            ).fetchall()
        return [dict(row) for row in rows]


_store: SessionSearchStore | None = None
_store_lock = threading.Lock()


def get_session_search_store(db_path: str | None = None) -> SessionSearchStore:
    global _store
    with _store_lock:
        resolved = _resolve_db_path(db_path)
        if _store is None or _store.db_path != resolved:
            _store = SessionSearchStore(db_path)
        return _store


def index_messages(*, thread_id: str, messages: list[Any], db_path: str | None = None) -> int:
    return get_session_search_store(db_path).index_messages(thread_id=thread_id, messages=messages)


def search_sessions(
    *,
    query: str | None = None,
    session_id: str | None = None,
    around_message_id: str | int | None = None,
    max_results: int = 5,
    db_path: str | None = None,
) -> dict[str, Any]:
    store = get_session_search_store(db_path)
    if session_id and around_message_id is not None:
        return {"mode": "around", "messages": store.around(session_id=session_id, around_message_id=around_message_id)}
    if query:
        return {"mode": "query", "results": store.search(query, max_results=max_results)}
    return {"mode": "recent", "sessions": store.recent_sessions(max_results=max_results)}
