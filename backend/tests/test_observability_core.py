from __future__ import annotations

import json

from config.observability_config import ObservabilityConfig
from observability.recorder import ObservabilityRecorder
from observability.redaction import sanitize_preview
from observability.store import ObservabilityStore


def test_background_events_are_queryable_with_filters_and_metadata(tmp_path, monkeypatch):
    import observability.web.app as web_app
    from config.paths import Paths

    monkeypatch.setattr(web_app, "get_paths", lambda: Paths(tmp_path / ".agentflow"))
    store = ObservabilityStore(base_dir=tmp_path / ".agentflow")
    store.insert_background_event(
        {
            "event_id": "event-1",
            "created_at": "2026-08-13T00:00:00+00:00",
            "event_type": "skill_create",
            "status": "completed",
            "thread_id": "thread-1",
            "parent_thread_id": "parent-1",
            "review_thread_id": "review-1",
            "agent_name": "agent-a",
            "skill": "python-debugging",
            "action": "create",
            "elapsed_ms": 12,
            "model_name": "review-model",
            "metadata": {"file_path": "SKILL.md"},
        }
    )

    data = web_app._load_self_improvement(skill="python-debugging", limit=1)
    assert data["total"] == 1
    assert data["events"][0]["event_id"] == "event-1"
    assert data["events"][0]["metadata"]["file_path"] == "SKILL.md"

    detail = web_app.api_self_improvement_event
    import asyncio

    result = asyncio.run(detail("event-1"))
    assert result["agent_name"] == "agent-a"
    assert "content" not in result["metadata"]


def test_redaction_masks_secrets_and_truncates():
    payload = "api_key=«redacted:sk-…» bearer Bearer abc.def.ghi"
    result = sanitize_preview(payload, max_chars=20)
    assert "[REDACTED]" in result["preview"]
    assert result["redacted"] is True
    assert result["truncated"] is True
    assert result["hash"]


def test_recorder_summary_mode_writes_thread_view_without_trace_file(tmp_path):
    root = tmp_path / ".agentflow"
    store = ObservabilityStore(base_dir=root)
    recorder = ObservabilityRecorder(
        config=ObservabilityConfig(
            enabled=True,
            content_mode="summary",
            capture_tool_args=True,
            capture_tool_results=True,
            max_preview_chars=200,
            export_trajectories=True,
            write_session_summary_view=True,
        ),
        store=store,
        bootstrap_legacy=False,
    )

    context, token = recorder.start_trace(
        thread_id="thread-one",
        agent_name="tester",
        model_name="deepseek-v4",
        user_input="Read file and explain it",
        content_mode="summary",
    )
    assert context is not None
    recorder.record_model_call(
        trace_id=context.trace_id,
        model_name="deepseek-v4",
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "billable_input_tokens": 15,
            "cache_read_input_tokens": 85,
            "cache_creation_input_tokens": 0,
            "prompt_cache_hit_tokens": 85,
            "prompt_cache_miss_tokens": 15,
            "prompt_cache_hit_rate": 0.85,
            "cache_signal_available": True,
        },
        preview_text="I should read the file first.",
        started_at="2026-07-05T00:00:00Z",
        ended_at="2026-07-05T00:00:01Z",
        elapsed_ms=1000,
    )
    recorder.record_tool_call(
        trace_id=context.trace_id,
        tool_call_id="call-1",
        tool_name="read_file",
        args_value={"path": "README.md"},
        result_value="file contents",
        status="ok",
        error_type=None,
        elapsed_ms=55,
        started_at="2026-07-05T00:00:00Z",
        ended_at="2026-07-05T00:00:01Z",
    )
    payload = recorder.end_trace(
        context,
        token,
        assistant_output="Here is the explanation.",
        completed=True,
    )
    assert payload is not None

    thread_path = store.threads_dir / "thread-one.json"
    session_log_path = root / "session_logs" / "thread-one.json"
    assert store.db_path.exists()
    assert thread_path.exists()
    assert session_log_path.exists()
    assert store.trajectory_success_path.exists()
    assert not any(store.traces_dir.rglob("*.json"))

    thread_totals = store.get_thread_totals("thread-one")
    assert thread_totals is not None
    assert thread_totals["totals"]["total_tokens"] == 120
    assert thread_totals["totals"]["tool_call_count"] == 1

    session_payload = json.loads(session_log_path.read_text(encoding="utf-8"))
    assert session_payload["summary"]["total_tokens"] == 120
    assert session_payload["turns"][-1]["trace_id"] == payload["trace_id"]

    lines = store.trajectory_success_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    trajectory = json.loads(lines[0])
    assert trajectory["completed"] is True
    assert trajectory["thread_id"] == "thread-one"


def test_recorder_full_mode_writes_detailed_trace_file(tmp_path):
    root = tmp_path / ".agentflow"
    store = ObservabilityStore(base_dir=root)
    recorder = ObservabilityRecorder(
        config=ObservabilityConfig(enabled=True, content_mode="full"),
        store=store,
        bootstrap_legacy=False,
    )
    context, token = recorder.start_trace(
        thread_id="thread-full",
        agent_name="tester",
        model_name="deepseek-v4",
        user_input="full mode trace",
        content_mode="full",
    )
    assert context is not None
    recorder.record_model_call(
        trace_id=context.trace_id,
        model_name="deepseek-v4",
        usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "billable_input_tokens": 2,
            "cache_read_input_tokens": 8,
            "cache_creation_input_tokens": 0,
            "prompt_cache_hit_tokens": 8,
            "prompt_cache_miss_tokens": 2,
            "prompt_cache_hit_rate": 0.8,
            "cache_signal_available": True,
        },
        preview_text="preview",
        started_at="2026-07-05T00:00:00Z",
        ended_at="2026-07-05T00:00:01Z",
        elapsed_ms=1000,
    )
    payload = recorder.end_trace(context, token, assistant_output="done", completed=True)
    assert payload is not None
    trace_files = list(store.traces_dir.rglob("*.json"))
    assert len(trace_files) == 1
    assert trace_files[0].name.endswith(f"{payload['trace_id'].split('-')[0]}.json")


def test_legacy_session_log_bootstrap(tmp_path):
    root = tmp_path / ".agentflow"
    session_logs_dir = root / "session_logs"
    session_logs_dir.mkdir(parents=True, exist_ok=True)
    legacy_payload = {
        "session_id": "legacy-thread",
        "started_at": "2026-07-03T00:00:00Z",
        "initial_agent_name": "legacy-agent",
        "initial_thread_id": "legacy-thread",
        "initial_model_name": "deepseek-v4",
        "turns": [{"turn_index": 1, "thread_id": "legacy-thread", "timestamp": "2026-07-03T00:00:01Z"}],
        "summary": {
            "turn_count": 1,
            "total_input_tokens": 10,
            "total_output_tokens": 5,
            "total_tokens": 15,
            "total_billable_input_tokens": 3,
            "total_prompt_cache_hit_tokens": 7,
            "total_prompt_cache_miss_tokens": 3,
        },
    }
    (session_logs_dir / "legacy-thread.json").write_text(json.dumps(legacy_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    store = ObservabilityStore(base_dir=root)
    recorder = ObservabilityRecorder(
        config=ObservabilityConfig(enabled=True),
        store=store,
        bootstrap_legacy=True,
    )
    summary = recorder.get_thread_summary("legacy-thread")
    assert summary["summary"]["total_tokens"] == 15
    assert summary["merged_legacy_files"] == ["legacy-thread.json"]


def test_recovered_failure_is_recorded_in_trace_and_thread_summary(tmp_path):
    root = tmp_path / ".agentflow"
    store = ObservabilityStore(base_dir=root)
    recorder = ObservabilityRecorder(
        config=ObservabilityConfig(enabled=True, content_mode="summary"),
        store=store,
        bootstrap_legacy=False,
    )

    context, token = recorder.start_trace(
        thread_id="thread-recovered",
        agent_name="tester",
        model_name="deepseek-v4",
        user_input="Open file and fix issue",
        content_mode="summary",
    )
    assert context is not None
    recorder.record_tool_call(
        trace_id=context.trace_id,
        tool_call_id="call-1",
        tool_name="read_file",
        args_value={"path": "missing.py"},
        result_value="file not found",
        status="error",
        error_type="FileNotFoundError",
        elapsed_ms=12,
        started_at="2026-07-05T00:00:00Z",
        ended_at="2026-07-05T00:00:01Z",
    )
    recorder.record_tool_call(
        trace_id=context.trace_id,
        tool_call_id="call-2",
        tool_name="read_file",
        args_value={"path": "app.py"},
        result_value="file contents",
        status="ok",
        error_type=None,
        elapsed_ms=20,
        started_at="2026-07-05T00:00:00Z",
        ended_at="2026-07-05T00:00:01Z",
    )

    payload = recorder.end_trace(
        context,
        token,
        assistant_output="Recovered and finished successfully.",
        completed=True,
    )
    assert payload is not None

    failure_summary = payload["summary"]
    assert failure_summary["had_any_failure"] is True
    assert failure_summary["failed_tool_call_count"] == 1
    assert failure_summary["failed_tool_names"] == ["read_file"]
    assert failure_summary["recovered_after_failure"] is True
    assert failure_summary["failure_events"] == [
        {
            "tool_name": "read_file",
            "tool_call_id": "call-1",
            "error_type": "FileNotFoundError",
            "elapsed_ms": 12,
            "status": "error",
        }
    ]

    thread_totals = store.get_thread_totals("thread-recovered")
    assert thread_totals is not None
    assert thread_totals["totals"]["trace_with_failures_count"] == 1
    assert thread_totals["totals"]["recovered_trace_count"] == 1
    assert thread_totals["totals"]["top_failed_tools"] == {"read_file": 1}
    assert thread_totals["totals"]["last_failed_tools"] == ["read_file"]
    assert thread_totals["totals"]["last_trace_had_any_failure"] is True
    assert thread_totals["totals"]["last_trace_recovered_after_failure"] is True

    summary = recorder.get_thread_summary("thread-recovered")
    assert summary["latest_trace"]["had_any_failure"] is True
    assert summary["latest_trace"]["recovered_after_failure"] is True
    assert summary["latest_trace"]["failed_tool_names"] == ["read_file"]
    assert summary["recent_traces"][-1]["failure_events"] == [
        {
            "tool_name": "read_file",
            "tool_call_id": "call-1",
            "error_type": "FileNotFoundError",
            "elapsed_ms": 12,
            "status": "error",
        }
    ]


def test_success_status_is_not_counted_as_failure_unless_result_reports_error(tmp_path):
    root = tmp_path / ".agentflow"
    store = ObservabilityStore(base_dir=root)
    recorder = ObservabilityRecorder(
        config=ObservabilityConfig(enabled=True, content_mode="summary"),
        store=store,
        bootstrap_legacy=False,
    )

    context, token = recorder.start_trace(
        thread_id="thread-success-status",
        agent_name="tester",
        model_name="deepseek-v4",
        user_input="Inspect project",
        content_mode="summary",
    )
    assert context is not None
    recorder.record_tool_call(
        trace_id=context.trace_id,
        tool_call_id="call-1",
        tool_name="ls",
        args_value={"path": "backend"},
        result_value="backend/observability/recorder.py",
        status="success",
        error_type="tool_result_error",
        elapsed_ms=10,
        started_at="2026-07-05T00:00:00Z",
        ended_at="2026-07-05T00:00:01Z",
    )
    recorder.record_tool_call(
        trace_id=context.trace_id,
        tool_call_id="call-2",
        tool_name="task",
        args_value={"description": "analyze"},
        result_value="Task failed. Error: Recursion limit of 10 reached without hitting a stop condition.",
        status="success",
        error_type="tool_result_error",
        elapsed_ms=1000,
        started_at="2026-07-05T00:00:00Z",
        ended_at="2026-07-05T00:00:01Z",
    )

    payload = recorder.end_trace(
        context,
        token,
        assistant_output="Recovered with direct inspection.",
        completed=True,
    )
    assert payload is not None

    failure_summary = payload["summary"]
    assert failure_summary["had_any_failure"] is True
    assert failure_summary["failed_tool_call_count"] == 1
    assert failure_summary["failed_tool_names"] == ["task"]
    assert failure_summary["failure_events"] == [
        {
            "tool_name": "task",
            "tool_call_id": "call-2",
            "error_type": "tool_result_error",
            "elapsed_ms": 1000,
            "status": "success",
        }
    ]


def test_unrecovered_failure_stays_visible(tmp_path):
    root = tmp_path / ".agentflow"
    store = ObservabilityStore(base_dir=root)
    recorder = ObservabilityRecorder(
        config=ObservabilityConfig(enabled=True, content_mode="summary"),
        store=store,
        bootstrap_legacy=False,
    )

    context, token = recorder.start_trace(
        thread_id="thread-failed",
        agent_name="tester",
        model_name="deepseek-v4",
        user_input="Call flaky tool",
        content_mode="summary",
    )
    assert context is not None
    recorder.record_tool_call(
        trace_id=context.trace_id,
        tool_call_id="call-1",
        tool_name="write_file",
        args_value={"path": "readonly.txt"},
        result_value="permission denied",
        status="error",
        error_type="PermissionError",
        elapsed_ms=18,
        started_at="2026-07-05T00:00:00Z",
        ended_at="2026-07-05T00:00:01Z",
    )

    payload = recorder.end_trace(
        context,
        token,
        assistant_output="",
        completed=False,
        failure_reason="PermissionError: denied",
    )
    assert payload is not None

    failure_summary = payload["summary"]
    assert failure_summary["had_any_failure"] is True
    assert failure_summary["failed_tool_call_count"] == 1
    assert failure_summary["failed_tool_names"] == ["write_file"]
    assert failure_summary["recovered_after_failure"] is False

    thread_totals = store.get_thread_totals("thread-failed")
    assert thread_totals is not None
    assert thread_totals["totals"]["trace_with_failures_count"] == 1
    assert thread_totals["totals"]["recovered_trace_count"] == 0
    assert thread_totals["totals"]["top_failed_tools"] == {"write_file": 1}
    assert thread_totals["totals"]["last_failed_tools"] == ["write_file"]

    summary = recorder.get_thread_summary("thread-failed")
    assert summary["latest_trace"]["had_any_failure"] is True
    assert summary["latest_trace"]["recovered_after_failure"] is False
    assert summary["latest_trace"]["failed_tool_names"] == ["write_file"]
