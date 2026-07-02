"""SQLite FTS5 storage for cross-session conversation search.

This is intentionally smaller than Hermes' session database. It stores a
searchable copy of user messages and final assistant replies, not full runtime
state.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from config.paths import get_paths, resolve_path

_UPLOAD_BLOCK_RE = re.compile(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff.-]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_MAX_CONTENT_CHARS = 1200
_SNIPPET_RADIUS = 80


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


def _iter_indexable_messages(messages: list[Any], *, index_assistant: bool = True) -> list[tuple[int, str, str, str | None]]:
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
        elif role == "assistant" and index_assistant:
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


def _truncate(text: str, limit: int = _MAX_CONTENT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _manual_snippet(content: str, query: str) -> str:
    if not content:
        return ""
    lowered = content.lower()
    terms = _TOKEN_RE.findall(query.lower())
    positions = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - _SNIPPET_RADIUS)
    end = min(len(content), pos + _SNIPPET_RADIUS)
    prefix = "..." if start else ""
    suffix = "..." if end < len(content) else ""
    return prefix + content[start:end].strip() + suffix


def _format_message_row(row: sqlite3.Row, *, include_content: bool = True) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "session_id": row["session_id"],
        "thread_id": row["thread_id"],
        "message_id": row["message_id"],
        "role": row["role"],
        "ts": row["ts"],
        "ordinal": row["ordinal"],
        "source": row["source"],
    }
    if include_content:
        result["content"] = _truncate(row["content"])
    return result


def _format_search_row(row: sqlite3.Row, *, query: str | None = None) -> dict[str, Any]:
    result = _format_message_row(row, include_content=False)
    snippet = row["snippet"] if "snippet" in row.keys() else None
    result["snippet"] = snippet or _manual_snippet(row["content"], query or "")
    return result


def _fts_query(query: str) -> str:
    tokens = [
        token
        for token in _TOKEN_RE.findall(query)
        if len(token) > 1 or _contains_cjk(token)
    ]
    if not tokens:
        return ""
    quoted = []
    for token in tokens[:12]:
        safe = token.replace('"', '""')
        quoted.append(f'"{safe}"')
    return " OR ".join(quoted)


def _contains_cjk(query: str) -> bool:
    return bool(_CJK_RE.search(query))


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
                    active INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'agent',
                    metadata TEXT
                )
                """
            )
            self._ensure_column(conn, "session_messages", "active", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "session_messages", "source", "TEXT NOT NULL DEFAULT 'agent'")
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS session_messages_fts
                USING fts5(content, role, session_id, content='session_messages', content_rowid='id')
                """
            )

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def index_messages(
        self,
        *,
        thread_id: str,
        messages: list[Any],
        session_id: str | None = None,
        source: str = "agent",
        index_assistant: bool = True,
    ) -> int:
        session_id = session_id or thread_id
        self.ensure_schema()
        inserted = 0
        now = _utc_now_iso()
        with self._lock, self._connection() as conn:
            for ordinal, role, content, message_id in _iter_indexable_messages(messages, index_assistant=index_assistant):
                key = _dedupe_key(session_id, role, ordinal, content, str(message_id) if message_id else None)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO session_messages
                        (dedupe_key, session_id, thread_id, message_id, role, content, ts, ordinal, active, source, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
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
                        source,
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
        try:
            rows = self._search_fts(fts_query, max_results=max_results)
        except sqlite3.OperationalError:
            rows = []
        if not rows and _contains_cjk(query):
            rows = self._search_like(query, max_results=max_results)
        return [_format_search_row(row, query=query) for row in rows]

    def _search_fts(self, fts_query: str, *, max_results: int) -> list[sqlite3.Row]:
        with self._lock, self._connection() as conn:
            return conn.execute(
                """
                SELECT
                    m.*,
                    snippet(session_messages_fts, 0, '>>>', '<<<', '...', 32) AS snippet,
                    bm25(session_messages_fts) AS score
                FROM session_messages_fts
                JOIN session_messages m ON m.id = session_messages_fts.rowid
                WHERE session_messages_fts MATCH ? AND m.active = 1
                ORDER BY score ASC, m.ts DESC
                LIMIT ?
                """,
                (fts_query, max_results),
            ).fetchall()

    def _search_like(self, query: str, *, max_results: int) -> list[sqlite3.Row]:
        pattern = f"%{query}%"
        with self._lock, self._connection() as conn:
            return conn.execute(
                """
                SELECT m.*, NULL AS snippet
                FROM session_messages m
                WHERE m.active = 1 AND m.content LIKE ?
                ORDER BY m.ts DESC, m.id DESC
                LIMIT ?
                """,
                (pattern, max_results),
            ).fetchall()

    def around(self, *, session_id: str, around_message_id: str | int, window: int = 3) -> dict[str, Any]:
        self.ensure_schema()
        window = max(1, min(int(window), 20))
        with self._lock, self._connection() as conn:
            anchor = conn.execute(
                """
                SELECT * FROM session_messages
                WHERE active = 1
                  AND session_id = ?
                  AND (message_id = ? OR CAST(id AS TEXT) = ?)
                ORDER BY ordinal ASC
                LIMIT 1
                """,
                (session_id, str(around_message_id), str(around_message_id)),
            ).fetchone()
            if anchor is None:
                return {"messages": [], "messages_before": 0, "messages_after": 0}
            start = max(0, int(anchor["ordinal"]) - window)
            end = int(anchor["ordinal"]) + window
            rows = conn.execute(
                """
                SELECT * FROM session_messages
                WHERE active = 1 AND session_id = ? AND ordinal BETWEEN ? AND ?
                ORDER BY ordinal ASC, id ASC
                """,
                (session_id, start, end),
            ).fetchall()
            before = conn.execute(
                "SELECT COUNT(*) AS count FROM session_messages WHERE active = 1 AND session_id = ? AND ordinal < ?",
                (session_id, start),
            ).fetchone()["count"]
            after = conn.execute(
                "SELECT COUNT(*) AS count FROM session_messages WHERE active = 1 AND session_id = ? AND ordinal > ?",
                (session_id, end),
            ).fetchone()["count"]
        return {
            "messages": [_format_message_row(row) for row in rows],
            "messages_before": before,
            "messages_after": after,
        }

    def read_session(self, session_id: str, *, head: int = 20, tail: int = 10) -> dict[str, Any]:
        self.ensure_schema()
        head = max(1, min(int(head), 100))
        tail = max(0, min(int(tail), 100))
        with self._lock, self._connection() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS count FROM session_messages WHERE active = 1 AND session_id = ?",
                (session_id,),
            ).fetchone()["count"]
            if total <= head + tail:
                rows = conn.execute(
                    """
                    SELECT * FROM session_messages
                    WHERE active = 1 AND session_id = ?
                    ORDER BY ordinal ASC, id ASC
                    """,
                    (session_id,),
                ).fetchall()
                omitted = 0
            else:
                first_rows = conn.execute(
                    """
                    SELECT * FROM session_messages
                    WHERE active = 1 AND session_id = ?
                    ORDER BY ordinal ASC, id ASC
                    LIMIT ?
                    """,
                    (session_id, head),
                ).fetchall()
                last_rows = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM session_messages
                        WHERE active = 1 AND session_id = ?
                        ORDER BY ordinal DESC, id DESC
                        LIMIT ?
                    )
                    ORDER BY ordinal ASC, id ASC
                    """,
                    (session_id, tail),
                ).fetchall()
                rows = first_rows + last_rows
                omitted = max(0, total - len(rows))
        return {
            "session_id": session_id,
            "messages": [_format_message_row(row) for row in rows],
            "message_count": total,
            "omitted_count": omitted,
        }

    def recent_sessions(self, *, max_results: int = 5) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    session_id,
                    thread_id,
                    source,
                    MAX(ts) AS last_ts,
                    COUNT(*) AS message_count,
                    (
                        SELECT content
                        FROM session_messages AS first_msg
                        WHERE first_msg.active = 1 AND first_msg.session_id = grouped.session_id
                        ORDER BY ordinal ASC, id ASC
                        LIMIT 1
                    ) AS preview
                FROM session_messages AS grouped
                WHERE active = 1
                GROUP BY session_id, thread_id, source
                ORDER BY last_ts DESC
                LIMIT ?
                """,
                (max_results,),
            ).fetchall()
        return [{**dict(row), "preview": _truncate(row["preview"] or "", 240)} for row in rows]

    def delete_session(self, session_id: str) -> int:
        self.ensure_schema()
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                "UPDATE session_messages SET active = 0 WHERE active = 1 AND session_id = ?",
                (session_id,),
            )
            return cursor.rowcount

    def prune_older_than(self, days: int) -> int:
        self.ensure_schema()
        cutoff = (datetime.now(UTC) - timedelta(days=max(0, int(days)))).isoformat()
        with self._lock, self._connection() as conn:
            cursor = conn.execute(
                "UPDATE session_messages SET active = 0 WHERE active = 1 AND ts < ?",
                (cutoff,),
            )
            return cursor.rowcount

    def vacuum(self) -> None:
        self.ensure_schema()
        with self._lock:
            conn = self._connect()
            try:
                conn.isolation_level = None
                conn.execute("VACUUM")
            finally:
                conn.close()


_store: SessionSearchStore | None = None
_store_lock = threading.Lock()


def get_session_search_store(db_path: str | None = None) -> SessionSearchStore:
    global _store
    with _store_lock:
        resolved = _resolve_db_path(db_path)
        if _store is None or _store.db_path != resolved:
            _store = SessionSearchStore(db_path)
        return _store


def index_messages(
    *,
    thread_id: str,
    messages: list[Any],
    db_path: str | None = None,
    index_assistant: bool = True,
) -> int:
    return get_session_search_store(db_path).index_messages(
        thread_id=thread_id,
        messages=messages,
        index_assistant=index_assistant,
    )


def search_sessions(
    *,
    query: str | None = None,
    session_id: str | None = None,
    around_message_id: str | int | None = None,
    max_results: int = 5,
    window: int = 3,
    db_path: str | None = None,
) -> dict[str, Any]:
    store = get_session_search_store(db_path)
    if session_id and around_message_id is not None:
        return {"mode": "around", **store.around(session_id=session_id, around_message_id=around_message_id, window=window)}
    if session_id:
        return {"mode": "read", **store.read_session(session_id)}
    if query:
        return {"mode": "query", "results": store.search(query, max_results=max_results)}
    return {"mode": "recent", "sessions": store.recent_sessions(max_results=max_results)}
