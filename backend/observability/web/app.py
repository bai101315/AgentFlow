"""Observability Web UI — single-page FastAPI app with dark/light theme.

Usage:
    PYTHONPATH=backend uv run python -m observability.web.app
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jinja2
from config.paths import get_paths
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_JINJA2_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
    cache_size=50,
)
app = FastAPI(title="DeerFlow Observability")
templates = Jinja2Templates(env=_JINJA2_ENV)


# ── WebSocket connections ──────────────────────────────────────────────

_ws_connections: set[WebSocket] = set()


@app.websocket("/ws")
async def ws_handler(ws: WebSocket) -> None:
    await ws.accept()
    _ws_connections.add(ws)
    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _ws_connections.discard(ws)


async def broadcast(type_: str, **data: Any) -> None:
    global _ws_connections
    payload = json.dumps({"type": type_, **data}, ensure_ascii=False, default=str)
    dead: set[WebSocket] = set()
    for ws in _ws_connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _ws_connections -= dead


# ── DB helpers ─────────────────────────────────────────────────────────

def _resolve_db() -> Path:
    path = get_paths().base_dir / "observability.db"
    if not path.exists():
        raise FileNotFoundError(f"observability.db not found at {path}")
    return path


def _connect() -> sqlite3.Connection:
    db = _resolve_db()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_background_events_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS background_events (
            event_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT,
            thread_id TEXT,
            parent_thread_id TEXT,
            review_thread_id TEXT,
            agent_name TEXT,
            skill TEXT,
            action TEXT,
            elapsed_ms INTEGER,
            model_name TEXT,
            metadata_json TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "n/a"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_rate(val: float | None) -> str:
    if val is None:
        return "n/a"
    return f"{val * 100:.1f}%"


def _fmt_date(iso_str: str | None) -> str:
    if not iso_str:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return iso_str[:16]


def _fmt_number(value: int | float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.0f}"


def _fmt_relative(iso_str: str | None) -> str:
    if not iso_str:
        return "未知时间"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(UTC) - dt.astimezone(UTC)).total_seconds()))
    except ValueError:
        return "未知时间"
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


# ── Data loaders ───────────────────────────────────────────────────────

def _load_overview_data() -> dict[str, Any]:
    conn = _connect()
    cur = conn.execute("""
        SELECT COUNT(*) AS total_traces,
               SUM(completed) AS completed_traces,
               COUNT(*) - SUM(completed) AS failed_traces,
               ROUND(AVG(elapsed_ms)) AS avg_elapsed_ms,
               ROUND(AVG(CAST(json_extract(usage_json, '$.input_tokens') AS INTEGER))) AS avg_input_tokens,
               ROUND(AVG(CAST(json_extract(usage_json, '$.output_tokens') AS INTEGER))) AS avg_output_tokens,
               SUM(CAST(json_extract(usage_json, '$.total_tokens') AS INTEGER)) AS total_tokens,
               SUM(CAST(json_extract(usage_json, '$.billable_input_tokens') AS INTEGER)) AS total_billable_input_tokens,
               ROUND(AVG(CAST(json_extract(usage_json, '$.prompt_cache_hit_rate') AS REAL)) * 100, 1) AS avg_cache_hit_rate
        FROM traces
    """).fetchone()

    daily = conn.execute("""
        SELECT substr(started_at, 1, 10) AS day,
               SUM(CAST(json_extract(usage_json, '$.input_tokens') AS INTEGER)) AS inp,
               SUM(CAST(json_extract(usage_json, '$.output_tokens') AS INTEGER)) AS out
        FROM traces GROUP BY day ORDER BY day
    """).fetchall()

    thread_count = conn.execute("SELECT COUNT(*) AS cnt FROM thread_totals").fetchone()["cnt"]
    conn.close()

    return {
        "total_traces": cur["total_traces"],
        "completed_traces": cur["completed_traces"],
        "failed_traces": cur["failed_traces"],
        "avg_elapsed_ms": _fmt_ms(cur["avg_elapsed_ms"]),
        "avg_input_tokens": _fmt_number(cur["avg_input_tokens"]),
        "avg_output_tokens": _fmt_number(cur["avg_output_tokens"]),
        "total_tokens": _fmt_number(cur["total_tokens"]),
        "total_billable": _fmt_number(cur["total_billable_input_tokens"]),
        "avg_cache_hit_rate": f"{cur['avg_cache_hit_rate']}%" if cur["avg_cache_hit_rate"] else "n/a",
        "thread_count": thread_count,
        "chart_dates": [row["day"] for row in daily],
        "chart_input": [int(row["inp"] or 0) for row in daily],
        "chart_output": [int(row["out"] or 0) for row in daily],
    }


def _load_recent_traces(limit: int = 20) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT trace_id, started_at, elapsed_ms, completed, user_input_preview, summary_json FROM traces ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    traces = []
    for r in rows:
        s = json.loads(r["summary_json"]) if r["summary_json"] else {}
        d = s.get("diagnostics", {})
        traces.append({
            "trace_id": r["trace_id"],
            "started_at": _fmt_relative(r["started_at"]),
            "started_at_absolute": r["started_at"],
            "elapsed_ms": _fmt_ms(r["elapsed_ms"]),
            "completed": bool(r["completed"]),
            "user_input": r["user_input_preview"],
            "diagnostic_reasons": d.get("reasons", []),
        })
    return traces


def _load_threads() -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT thread_id, totals_json, metadata_json, updated_at FROM thread_totals ORDER BY updated_at DESC LIMIT 50"
    ).fetchall()
    conn.close()
    threads = []
    for r in rows:
        totals = json.loads(r["totals_json"]) if r["totals_json"] else {}
        metadata = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
        threads.append({
            "thread_id": r["thread_id"],
            "agent": metadata.get("initial_agent_name", "—"),
            "trace_count": totals.get("trace_count", 0),
            "turn_count": totals.get("turn_count", 0),
            "total_tokens": _fmt_number(totals.get("total_tokens", 0)),
            "cache_hit_rate": _fmt_rate(totals.get("prompt_cache_hit_rate")),
            "tool_call_count": totals.get("tool_call_count", 0),
            "failed_tool_call_count": totals.get("failed_tool_call_count", 0),
            "updated_at": _fmt_relative(r["updated_at"]),
            "updated_at_absolute": r["updated_at"],
        })
    return threads


def _load_stats() -> dict[str, Any]:
    conn = _connect()
    cur = conn.execute("""
        SELECT COUNT(*) AS total_traces,
               SUM(completed) AS completed_traces,
               COUNT(*) - SUM(completed) AS failed_traces,
               ROUND(AVG(elapsed_ms)) AS avg_elapsed_ms,
               ROUND(AVG(CAST(json_extract(usage_json, '$.input_tokens') AS INTEGER))) AS avg_input_tokens,
               ROUND(AVG(CAST(json_extract(usage_json, '$.output_tokens') AS INTEGER))) AS avg_output_tokens,
               SUM(CAST(json_extract(usage_json, '$.total_tokens') AS INTEGER)) AS total_tokens,
               SUM(CAST(json_extract(usage_json, '$.billable_input_tokens') AS INTEGER)) AS total_billable_input_tokens,
               ROUND(AVG(CAST(json_extract(usage_json, '$.prompt_cache_hit_rate') AS REAL)) * 100, 1) AS avg_cache_hit_rate
        FROM traces
    """).fetchone()

    rows = conn.execute("SELECT summary_json FROM traces").fetchall()
    slow = pricey = nocache = 0
    for row in rows:
        s = json.loads(row["summary_json"]) if row["summary_json"] else {}
        reasons = s.get("diagnostics", {}).get("reasons", [])
        if "slow_trace" in reasons:
            slow += 1
        if "high_billable_input_tokens" in reasons:
            pricey += 1
        if "low_cache_hit_rate" in reasons:
            nocache += 1

    daily = conn.execute("""
        SELECT substr(started_at, 1, 10) AS day,
               SUM(CAST(json_extract(usage_json, '$.input_tokens') AS INTEGER)) AS inp,
               SUM(CAST(json_extract(usage_json, '$.output_tokens') AS INTEGER)) AS out,
               AVG(CAST(json_extract(usage_json, '$.prompt_cache_hit_rate') AS REAL)) * 100 AS cache
        FROM traces GROUP BY day ORDER BY day
    """).fetchall()
    conn.close()

    return {
        "total_traces": cur["total_traces"],
        "completed_traces": cur["completed_traces"],
        "failed_traces": cur["failed_traces"],
        "avg_elapsed_ms": _fmt_ms(cur["avg_elapsed_ms"]),
        "avg_input_tokens": _fmt_number(cur["avg_input_tokens"]),
        "avg_output_tokens": _fmt_number(cur["avg_output_tokens"]),
        "total_tokens": _fmt_number(cur["total_tokens"]),
        "total_billable": _fmt_number(cur["total_billable_input_tokens"]),
        "avg_cache_hit_rate": f"{cur['avg_cache_hit_rate']}%" if cur["avg_cache_hit_rate"] else "n/a",
        "slow_count": slow, "pricey_count": pricey, "nocache_count": nocache,
        "chart_dates": [row["day"] for row in daily],
        "chart_input": [int(row["inp"] or 0) for row in daily],
        "chart_output": [int(row["out"] or 0) for row in daily],
        "chart_cache": [round(float(row["cache"] or 0), 1) for row in daily],
    }


def _load_thread_detail(thread_id: str) -> dict | None:
    conn = _connect()
    tr = conn.execute(
        "SELECT totals_json, metadata_json FROM thread_totals WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    if not tr:
        conn.close()
        return None
    totals = json.loads(tr["totals_json"]) if tr["totals_json"] else {}
    metadata = json.loads(tr["metadata_json"]) if tr["metadata_json"] else {}

    traces_raw = conn.execute(
        "SELECT trace_id, started_at, elapsed_ms, completed, failure_reason, user_input_preview, assistant_output_preview, summary_json FROM traces WHERE thread_id = ? ORDER BY started_at DESC LIMIT 50",
        (thread_id,),
    ).fetchall()

    traces = []
    for t in traces_raw:
        s = json.loads(t["summary_json"]) if t["summary_json"] else {}
        tc_rows = conn.execute("SELECT tool_name FROM tool_calls WHERE trace_id = ?", (t["trace_id"],)).fetchall()
        tool_names = list({tc["tool_name"] for tc in tc_rows})
        traces.append({
            "trace_id": t["trace_id"],
            "started_at": _fmt_relative(t["started_at"]),
            "started_at_absolute": t["started_at"],
            "elapsed_ms": _fmt_ms(t["elapsed_ms"]),
            "completed": bool(t["completed"]),
            "failure_reason": t["failure_reason"],
            "user_input": t["user_input_preview"],
            "assistant_output": t["assistant_output_preview"],
            "tool_names": tool_names,
            "had_any_failure": s.get("had_any_failure", False),
            "recovered_after_failure": s.get("recovered_after_failure", False),
        })
    conn.close()

    return {
        "thread_id": thread_id,
        "agent": metadata.get("initial_agent_name", "—"),
        "model": metadata.get("initial_model_name", "—"),
        "trace_count": totals.get("trace_count", 0),
        "turn_count": totals.get("turn_count", 0),
        "total_tokens": _fmt_number(totals.get("total_tokens", 0)),
        "cache_hit_rate": _fmt_rate(totals.get("prompt_cache_hit_rate")),
        "tool_call_count": totals.get("tool_call_count", 0),
        "failed_tool_call_count": totals.get("failed_tool_call_count", 0),
        "top_failed_tools": totals.get("top_failed_tools", {}),
        "traces": traces,
    }


def _load_trace_detail(trace_id: str) -> dict | None:
    conn = _connect()
    trace = conn.execute("SELECT * FROM traces WHERE trace_id = ? OR trace_id LIKE ?", (trace_id, f"{trace_id}%")).fetchone()
    if not trace:
        conn.close()
        return None

    usage = json.loads(trace["usage_json"]) if trace["usage_json"] else {}

    mcs = conn.execute("SELECT model_name, usage_json, started_at, elapsed_ms FROM model_calls WHERE trace_id = ? ORDER BY started_at ASC", (trace["trace_id"],)).fetchall()
    model_calls = []
    for mc in mcs:
        mu = json.loads(mc["usage_json"]) if mc["usage_json"] else {}
        model_calls.append({
            "model_name": mc["model_name"],
            "started_at": mc["started_at"],
            "elapsed_ms": _fmt_ms(mc["elapsed_ms"]),
            "input_tokens": mu.get("input_tokens", 0),
            "output_tokens": mu.get("output_tokens", 0),
            "cache_hit_rate": _fmt_rate(mu.get("prompt_cache_hit_rate")),
        })

    tcs = conn.execute("SELECT tc.payload_json, s.started_at FROM tool_calls tc JOIN spans s ON s.span_id = tc.span_id WHERE tc.trace_id = ? ORDER BY s.started_at ASC", (trace["trace_id"],)).fetchall()
    tool_calls = []
    for tc_raw in tcs:
        tc = json.loads(tc_raw["payload_json"])
        tool_calls.append({
            "tool_name": tc.get("tool_name", "?"),
            "started_at": tc_raw["started_at"],
            "elapsed_ms": _fmt_ms(tc.get("elapsed_ms")),
            "status": tc.get("status", "ok"),
            "args_preview": tc.get("args_preview", ""),
            "result_preview": tc.get("result_preview", ""),
        })

    ces = conn.execute("SELECT cache_name, hit_tokens, miss_tokens, created_at FROM cache_events WHERE trace_id = ? ORDER BY created_at ASC", (trace["trace_id"],)).fetchall()
    cache_events = [{"cache_name": ce["cache_name"], "hit_tokens": ce["hit_tokens"], "miss_tokens": ce["miss_tokens"], "created_at": ce["created_at"]} for ce in ces]
    conn.close()

    nodes = [
        {"kind": "llm", **call}
        for call in model_calls
    ] + [
        {"kind": "tool", **call}
        for call in tool_calls
    ]
    nodes.sort(key=lambda node: node.get("started_at") or "")

    return {
        "trace_id": trace["trace_id"],
        "thread_id": trace["thread_id"],
        "model_name": trace["model_name"],
        "started_at": trace["started_at"],
        "elapsed_ms": _fmt_ms(trace["elapsed_ms"]),
        "completed": bool(trace["completed"]),
        "failure_reason": trace["failure_reason"],
        "input_tokens": _fmt_number(usage.get("input_tokens")),
        "output_tokens": _fmt_number(usage.get("output_tokens")),
        "billable_input_tokens": _fmt_number(usage.get("billable_input_tokens")),
        "cache_hit_rate": _fmt_rate(usage.get("prompt_cache_hit_rate")),
        "user_input": trace["user_input_preview"],
        "assistant_output": trace["assistant_output_preview"],
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "nodes": nodes,
        "cache_events": cache_events,
    }


def _load_self_improvement(
    *,
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    status: str | None = None,
    thread_id: str | None = None,
    skill: str | None = None,
) -> dict[str, Any]:
    conn = _connect()
    _ensure_background_events_table(conn)
    clauses: list[str] = []
    params: list[Any] = []
    for field, value in (("event_type", event_type), ("status", status), ("thread_id", thread_id), ("skill", skill)):
        if value:
            clauses.append(f"{field} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM background_events {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (*params, max(1, min(limit, 200)), max(0, offset)),
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) AS count FROM background_events {where}", tuple(params)).fetchone()["count"]
    metrics = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN event_type = 'review_completed' AND status = 'completed' THEN 1 ELSE 0 END) AS review_completed,
          SUM(CASE WHEN event_type = 'review_failed' THEN 1 ELSE 0 END) AS review_failed,
          SUM(CASE WHEN event_type = 'review_dropped' THEN 1 ELSE 0 END) AS review_dropped,
          SUM(CASE WHEN event_type = 'memory_completed' THEN 1 ELSE 0 END) AS memory_completed,
          SUM(CASE WHEN event_type = 'memory_rejected' THEN 1 ELSE 0 END) AS memory_rejected,
          SUM(CASE WHEN event_type = 'memory_dropped' THEN 1 ELSE 0 END) AS memory_dropped,
          SUM(CASE WHEN event_type LIKE 'skill_%' THEN 1 ELSE 0 END) AS skill_events
        FROM background_events
        """
    ).fetchone()
    conn.close()
    events = []
    for row in rows:
        events.append({
            "event_id": row["event_id"], "created_at": row["created_at"],
            "event_type": row["event_type"], "status": row["status"],
            "thread_id": row["thread_id"], "parent_thread_id": row["parent_thread_id"],
            "review_thread_id": row["review_thread_id"], "agent_name": row["agent_name"],
            "skill": row["skill"], "action": row["action"],
            "elapsed_ms": row["elapsed_ms"], "model_name": row["model_name"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        })
    return {"metrics": {key: int(metrics[key] or 0) for key in metrics.keys()}, "events": events, "total": total, "limit": limit, "offset": offset}


# ── API endpoints (JSON for HTMX) ──────────────────────────────────────


@app.get("/api/overview")
async def api_overview():
    return _load_overview_data()


@app.get("/api/recent")
async def api_recent():
    return {"traces": _load_recent_traces()}


@app.get("/api/threads")
async def api_threads():
    return {"threads": _load_threads()}


@app.get("/api/stats")
async def api_stats():
    return _load_stats()


@app.get("/api/thread/{thread_id}")
async def api_thread(thread_id: str):
    data = _load_thread_detail(thread_id)
    if data is None:
        return {"error": "not found"}
    return data


@app.get("/api/trace/{trace_id}")
async def api_trace(trace_id: str):
    data = _load_trace_detail(trace_id)
    if data is None:
        return {"error": "not found"}
    return data


@app.get("/api/self-improvement")
async def api_self_improvement(
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    status: str | None = None,
    thread_id: str | None = None,
    skill: str | None = None,
):
    return _load_self_improvement(
        limit=limit, offset=offset, event_type=event_type, status=status,
        thread_id=thread_id, skill=skill,
    )


@app.get("/api/self-improvement/events/{event_id}")
async def api_self_improvement_event(event_id: str):
    conn = _connect()
    _ensure_background_events_table(conn)
    row = conn.execute("SELECT * FROM background_events WHERE event_id = ?", (event_id,)).fetchone()
    conn.close()
    if row is None:
        return {"error": "not found"}
    return {
        "event_id": row["event_id"], "created_at": row["created_at"],
        "event_type": row["event_type"], "status": row["status"],
        "thread_id": row["thread_id"], "parent_thread_id": row["parent_thread_id"],
        "review_thread_id": row["review_thread_id"], "agent_name": row["agent_name"],
        "skill": row["skill"], "action": row["action"], "elapsed_ms": row["elapsed_ms"],
        "model_name": row["model_name"], "metadata": json.loads(row["metadata_json"] or "{}"),
    }


# ── Main page ──────────────────────────────────────────────────────────


@app.get("/{path:path}", response_class=HTMLResponse)
async def index(request: Request, path: str = ""):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request},
    )


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8081, log_level="info")


if __name__ == "__main__":
    main()
