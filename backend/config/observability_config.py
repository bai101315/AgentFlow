from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ObservabilityContentMode = Literal["summary", "full", "off"]


class ObservabilityConfig(BaseModel):
    """Configuration for the local observability subsystem."""

    enabled: bool = Field(default=True, description="Enable local observability capture.")
    content_mode: ObservabilityContentMode = Field(
        default="summary",
        description="Default content capture mode: summary, full, or off.",
    )
    capture_tool_args: bool = Field(default=True, description="Capture redacted tool argument previews.")
    capture_tool_results: bool = Field(default=True, description="Capture redacted tool result previews.")
    max_preview_chars: int = Field(default=1200, description="Maximum preview length for captured text.")
    export_trajectories: bool = Field(default=True, description="Export trajectories to JSONL files.")
    write_session_summary_view: bool = Field(
        default=True,
        description="Write human-readable thread/session summary files under session_logs.",
    )
    write_trace_files_on_failure: bool = Field(
        default=True,
        description="Write a detailed trace JSON file when a trace fails.",
    )
    write_trace_files_on_full: bool = Field(
        default=True,
        description="Write a detailed trace JSON file when the capture mode is full.",
    )
    write_trace_files_on_anomaly: bool = Field(
        default=True,
        description="Write a detailed trace JSON file when runtime diagnostics cross warning thresholds.",
    )
    slow_trace_ms: int = Field(
        default=10000,
        description="Trace duration threshold that marks a run as slow.",
    )
    high_billable_input_tokens: int = Field(
        default=4000,
        description="Billable input token threshold that marks a run as expensive.",
    )
    low_cache_hit_rate: float = Field(
        default=0.5,
        description="Prompt cache hit rate threshold below which a run is marked as cache-inefficient.",
    )
