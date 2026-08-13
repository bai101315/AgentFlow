"""Observability CLI — query, tail, and inspect traces from the terminal.

Usage:
    uv run python -m observability.cli stats          # session-level aggregates
    uv run python -m observability.cli recent          # recent traces (last 20)
    uv run python -m observability.cli traces          # all traces (paginated)
    uv run python -m observability.cli tail            # live tail new traces
    uv run python -m observability.cli trace <id>      # detail of one trace
    uv run python -m observability.cli threads         # list all threads
    uv run python -m observability.cli thread <id>     # summary of a thread
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


def _resolve_db() -> Path:
    """Find observability.db under the project's data directory."""
    # Try common locations
    candidates = [
        Path.home() / ".agentflow" / "observability.db",
        Path.home() / ".deerflow" / "observability.db",
        Path.cwd() / ".agentflow" / "observability.db",
        Path.cwd() / "data" / "observability.db",
        Path.cwd().resolve() / ".agentflow" / "observability.db",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Fallback: check env var
    env_path = None
    try:
        from os import environ

        env_path = environ.get("DEERFLOW_DATA_DIR")
    except ImportError:
        pass
    if env_path:
        p = Path(env_path) / "observability.db"
        if p.exists():
            return p
    print("Error: observability.db not found. Tried:", file=sys.stderr)
    for p in candidates:
        print(f"  {p}", file=sys.stderr)
    sys.exit(1)


def _connect() -> sqlite3.Connection:
    db = _resolve_db()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


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


def _fmt_short_id(trace_id: str) -> str:
    return trace_id.split("-")[0] if trace_id else "?"


def _fmt_time(iso_str: str | None) -> str:
    if not iso_str:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M:%S")
    except ValueError:
        return iso_str[:19]


def _fmt_date(iso_str: str | None) -> str:
    if not iso_str:
        return "n/a"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return iso_str[:16]


# ── Commands ──────────────────────────────────────────────────────────


def cmd_stats(args: argparse.Namespace) -> None:
    conn = _connect()
    cur = conn.execute(
        """
        SELECT
            COUNT(*) AS total_traces,
            SUM(completed) AS completed_traces,
            COUNT(*) - SUM(completed) AS failed_traces,
            ROUND(AVG(elapsed_ms)) AS avg_elapsed_ms,
            ROUND(AVG(CAST(json_extract(usage_json, '$.input_tokens') AS INTEGER))) AS avg_input_tokens,
            ROUND(AVG(CAST(json_extract(usage_json, '$.output_tokens') AS INTEGER))) AS avg_output_tokens,
            SUM(CAST(json_extract(usage_json, '$.total_tokens') AS INTEGER)) AS total_tokens,
            SUM(CAST(json_extract(usage_json, '$.billable_input_tokens') AS INTEGER)) AS total_billable_input_tokens,
            ROUND(AVG(CAST(json_extract(usage_json, '$.prompt_cache_hit_rate') AS REAL)) * 100, 1) AS avg_cache_hit_rate
        FROM traces
    """
    ).fetchone()
    conn.close()

    if cur and cur["total_traces"]:
        row = cur
        print(f"  Total traces:     {row['total_traces']}")
        print(f"  Completed:        {row['completed_traces']}")
        print(f"  Failed:           {row['failed_traces']}")
        print(f"  Avg duration:     {_fmt_ms(row['avg_elapsed_ms'])}")
        print(f"  Avg input tokens: {_fmt_tokens(row['avg_input_tokens'])}")
        print(f"  Avg output tokens:{_fmt_tokens(row['avg_output_tokens'])}")
        print(f"  Total tokens:     {_fmt_tokens(row['total_tokens'])}")
        print(f"  Total billable:   {_fmt_tokens(row['total_billable_input_tokens'])}")
        print(f"  Avg cache hit:    {row['avg_cache_hit_rate']}%" if row['avg_cache_hit_rate'] else "  Avg cache hit:    n/a")
    else:
        print("  No traces recorded yet.")


def cmd_recent(args: argparse.Namespace) -> None:
    conn = _connect()
    limit = getattr(args, "limit", 20)
    rows = conn.execute(
        """
        SELECT trace_id, started_at, elapsed_ms, completed, failure_reason,
               user_input_preview, summary_json
        FROM traces
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        print("  No traces recorded yet.")
        return

    print(f"  Recent {len(rows)} traces:")
    print()
    for r in rows:
        summary = json.loads(r["summary_json"]) if r["summary_json"] else {}
        diagnostics = summary.get("diagnostics", {})
        reasons = diagnostics.get("reasons", [])
        flags = "!"
        # if not r["completed"]: flags += "F"
        if not r["completed"]:
            flags += "FAIL"
        if "slow_trace" in reasons:
            flags += "SLOW"
        if "high_billable_input_tokens" in reasons:
            flags += "PRICEY"
        if "low_cache_hit_rate" in reasons:
            flags += "NOCACHE"
        if flags == "!":
            flags = "OK"

        preview = (r["user_input_preview"] or "")[:80]
        print(f"  {_fmt_short_id(r['trace_id']):>8}  {_fmt_date(r['started_at'])}  "
              f"{_fmt_ms(r['elapsed_ms']):>8}  [{flags:>8}]  {preview}")


def cmd_traces(args: argparse.Namespace) -> None:
    conn = _connect()
    page = getattr(args, "page", 1)
    page_size = getattr(args, "page_size", 30)
    offset = (page - 1) * page_size

    rows = conn.execute(
        """
        SELECT trace_id, thread_id, started_at, elapsed_ms, completed,
               failure_reason, user_input_preview, summary_json
        FROM traces
        ORDER BY started_at DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, offset),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS cnt FROM traces").fetchone()["cnt"]
    conn.close()

    if not rows:
        print("  No traces on this page.")
        return

    print(f"  Page {page} (showing {len(rows)} of {total} traces)")
    print()
    for r in rows:
        summary = json.loads(r["summary_json"]) if r["summary_json"] else {}
        diagnostics = summary.get("diagnostics", {})
        reasons = diagnostics.get("reasons", [])
        tags = []
        if not r["completed"]:
            tags.append("FAIL")
        if "slow_trace" in reasons:
            tags.append("SLOW")
        if "high_billable_input_tokens" in reasons:
            tags.append("PRICEY")
        if "low_cache_hit_rate" in reasons:
            tags.append("NOCACHE")
        tag_str = " ".join(tags) if tags else "OK"

        preview = (r["user_input_preview"] or "")[:80]
        print(f"  {_fmt_short_id(r['trace_id']):>8}  {r['thread_id'][:20]:>20}  "
              f"{_fmt_date(r['started_at'])}  {_fmt_ms(r['elapsed_ms']):>8}  [{tag_str}]  {preview}")


def cmd_trace(args: argparse.Namespace) -> None:
    trace_id = args.trace_id
    conn = _connect()

    trace = conn.execute(
        """
        SELECT * FROM traces WHERE trace_id = ? OR trace_id LIKE ?
        """,
        (trace_id, f"{trace_id}%"),
    ).fetchone()
    if not trace:
        print(f"  Trace '{trace_id}' not found.")
        conn.close()
        return

    usage = json.loads(trace["usage_json"]) if trace["usage_json"] else {}

    print(f"  Trace:    {trace['trace_id']}")
    print(f"  Thread:   {trace['thread_id']}")
    print(f"  Agent:    {trace['agent_name'] or 'n/a'}")
    print(f"  Model:    {trace['model_name'] or 'n/a'}")
    print(f"  Started:  {trace['started_at']}")
    print(f"  Ended:    {trace['ended_at'] or 'n/a'}")
    print(f"  Duration: {_fmt_ms(trace['elapsed_ms'])}")
    print(f"  Status:   {'completed' if trace['completed'] else 'FAILED'}")
    if trace["failure_reason"]:
        print(f"  Reason:   {trace['failure_reason']}")
    print()
    print(f"  Input tokens:              {_fmt_tokens(usage.get('input_tokens'))}")
    print(f"  Output tokens:             {_fmt_tokens(usage.get('output_tokens'))}")
    print(f"  Total tokens:              {_fmt_tokens(usage.get('total_tokens'))}")
    print(f"  Billable input tokens:     {_fmt_tokens(usage.get('billable_input_tokens'))}")
    print(f"  Prompt cache hit rate:     {_fmt_rate(usage.get('prompt_cache_hit_rate'))}")
    print()

    # Tool calls
    tool_calls = conn.execute(
        "SELECT payload_json FROM tool_calls WHERE trace_id = ? ORDER BY elapsed_ms DESC",
        (trace["trace_id"],),
    ).fetchall()
    conn.close()

    if tool_calls:
        print(f"  Tool calls ({len(tool_calls)}):")
        for tc_raw in tool_calls:
            tc = json.loads(tc_raw["payload_json"])
            status = tc.get("status", "ok")
            tag = "OK" if status == "ok" else "ERR"
            print(f"    {tc.get('tool_name', '?'):>20}  {_fmt_ms(tc.get('elapsed_ms')):>8}  [{tag}]  "
                  f"args={str(tc.get('args_preview', ''))[:60]}")
    else:
        print("  No tool calls recorded.")

    # Show user input / output previews
    if trace["user_input_preview"]:
        print(f"\n  User input:  {trace['user_input_preview'][:200]}")
    if trace["assistant_output_preview"]:
        print(f"  Assistant:   {trace['assistant_output_preview'][:200]}")
    print()


def cmd_threads(args: argparse.Namespace) -> None:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT thread_id, totals_json, metadata_json, updated_at
        FROM thread_totals
        ORDER BY updated_at DESC
        LIMIT 30
        """
    ).fetchall()
    conn.close()

    if not rows:
        print("  No threads recorded yet.")
        return

    print(f"  {'Thread ID':<40} {'Traces':>6} {'Tokens':>10} {'Cache Hit':>10} {'Updated'}")
    print(f"  {'-'*40} {'-'*6} {'-'*10} {'-'*10} {'-'*10}")
    for r in rows:
        totals = json.loads(r["totals_json"]) if r["totals_json"] else {}
        print(f"  {r['thread_id']:<40} {totals.get('trace_count', 0):>6} "
              f"{_fmt_tokens(totals.get('total_tokens', 0)):>10} "
              f"{_fmt_rate(totals.get('prompt_cache_hit_rate')):>10} "
              f"{_fmt_date(r['updated_at'])}")


def cmd_thread(args: argparse.Namespace) -> None:
    thread_id = args.thread_id
    conn = _connect()

    totals_row = conn.execute(
        "SELECT totals_json, metadata_json, updated_at FROM thread_totals WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    if not totals_row:
        print(f"  Thread '{thread_id}' not found.")
        conn.close()
        return

    totals = json.loads(totals_row["totals_json"]) if totals_row["totals_json"] else {}
    metadata = json.loads(totals_row["metadata_json"]) if totals_row["metadata_json"] else {}

    print(f"  Thread:     {thread_id}")
    print(f"  Agent:      {metadata.get('initial_agent_name', 'n/a')}")
    print(f"  Model:      {metadata.get('initial_model_name', 'n/a')}")
    print(f"  Started:    {metadata.get('started_at', 'n/a')}")
    print(f"  Updated:    {totals_row['updated_at']}")
    print()
    print(f"  Traces:             {totals.get('trace_count', 0)}")
    print(f"  Turns:              {totals.get('turn_count', 0)}")
    print(f"  Total tokens:       {_fmt_tokens(totals.get('total_tokens', 0))}")
    print(f"  Total billable:     {_fmt_tokens(totals.get('total_billable_input_tokens', 0))}")
    print(f"  Cache hit rate:     {_fmt_rate(totals.get('prompt_cache_hit_rate'))}")
    print(f"  Tool calls:         {totals.get('tool_call_count', 0)}")
    print(f"  Failed tool calls:  {totals.get('failed_tool_call_count', 0)}")
    print(f"  Trace failures:     {totals.get('trace_with_failures_count', 0)}")
    print(f"  Recovered:          {totals.get('recovered_trace_count', 0)}")
    print(f"  Slow traces:        {totals.get('slow_trace_count', 0)}")
    print(f"  Expensive traces:   {totals.get('expensive_trace_count', 0)}")
    print(f"  Low-cache traces:   {totals.get('low_cache_trace_count', 0)}")

    top_failed = totals.get("top_failed_tools", {})
    if top_failed:
        print("\n  Top failed tools:")
        for tool, count in sorted(top_failed.items(), key=lambda x: -x[1]):
            print(f"    {tool:<20} {count} failures")

    # List recent traces for this thread
    traces = conn.execute(
        "SELECT trace_id, started_at, elapsed_ms, completed, user_input_preview FROM traces WHERE thread_id = ? ORDER BY started_at DESC LIMIT 10",
        (thread_id,),
    ).fetchall()
    conn.close()

    if traces:
        print("\n  Recent traces:")
        for tr in traces:
            preview = (tr["user_input_preview"] or "")[:60]
            status = "OK" if tr["completed"] else "FAIL"
            print(f"    {_fmt_short_id(tr['trace_id']):>8}  {_fmt_date(tr['started_at'])}  "
                  f"{_fmt_ms(tr['elapsed_ms']):>8}  [{status}]  {preview}")


def cmd_tail(args: argparse.Namespace) -> None:
    """Live tail — watch for new traces as they appear."""
    db = _resolve_db()
    interval = getattr(args, "interval", 2.0)

    # Get the latest trace_id we know about
    conn = _connect()
    last_id = conn.execute("SELECT trace_id FROM traces ORDER BY started_at DESC LIMIT 1").fetchone()
    conn.close()
    seen: set[str] = {last_id["trace_id"]} if last_id else set()

    print(f"  Watching {db} for new traces (poll every {interval}s)...")
    print(f"  {'ID':>8}  {'Time':>8}  {'Dur':>8}  {'Status':>8}  {'Input'}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*60}")

    try:
        while True:
            conn = _connect()
            rows = conn.execute(
                "SELECT trace_id, started_at, elapsed_ms, completed, user_input_preview, summary_json FROM traces ORDER BY started_at DESC LIMIT 10"
            ).fetchall()
            conn.close()

            for r in rows:
                tid = r["trace_id"]
                if tid in seen:
                    continue
                seen.add(tid)

                summary = json.loads(r["summary_json"]) if r["summary_json"] else {}
                diagnostics = summary.get("diagnostics", {})
                reasons = diagnostics.get("reasons", [])

                if not r["completed"]:
                    tag = "FAIL"
                elif "low_cache_hit_rate" in reasons:
                    tag = "NOCACHE"
                elif "high_billable_input_tokens" in reasons:
                    tag = "PRICEY"
                elif "slow_trace" in reasons:
                    tag = "SLOW"
                else:
                    tag = "OK"

                preview = (r["user_input_preview"] or "")[:60]

                print(f"  {_fmt_short_id(tid):>8}  {_fmt_time(r['started_at']):>8}  "
                      f"{_fmt_ms(r['elapsed_ms']):>8}  [{tag:>8}]  {preview}")

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Stopped.")


# ── Main ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Observability CLI — inspect traces, threads, and live runs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  obs stats                    # session-level aggregates
  obs recent                   # recent 20 traces
  obs recent -n 50             # recent 50 traces
  obs traces                   # all traces (paginated, 30/page)
  obs traces --page 2          # page 2
  obs tail                     # live tail new traces
  obs tail -i 1                # poll every 1s
  obs trace abc123             # detail of one trace
  obs threads                  # list all threads
  obs thread my-thread         # thread summary
        """,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", help="Session-level aggregate stats")  # noqa: F841

    p_recent = sub.add_parser("recent", help="Recent traces")
    p_recent.add_argument("-n", "--limit", type=int, default=20, help="Number of traces (default: 20)")

    p_traces = sub.add_parser("traces", help="All traces (paginated)")
    p_traces.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    p_traces.add_argument("--page-size", type=int, default=30, help="Page size (default: 30)")

    p_trace = sub.add_parser("trace", help="Detail of one trace")
    p_trace.add_argument("trace_id", help="Trace ID (full or prefix)")

    p_threads = sub.add_parser("threads", help="List all threads")  # noqa: F841
    p_thread = sub.add_parser("thread", help="Summary of a thread")
    p_thread.add_argument("thread_id", help="Thread ID")

    p_tail = sub.add_parser("tail", help="Live tail new traces")
    p_tail.add_argument("-i", "--interval", type=float, default=2.0, help="Poll interval in seconds (default: 2)")

    args = parser.parse_args()

    cmds = {
        "stats": cmd_stats,
        "recent": cmd_recent,
        "traces": cmd_traces,
        "trace": cmd_trace,
        "threads": cmd_threads,
        "thread": cmd_thread,
        "tail": cmd_tail,
    }

    cmds[args.command](args)


if __name__ == "__main__":
    main()