from pydantic import BaseModel, Field, model_validator


class BackgroundReviewConfig(BaseModel):
    """Configuration for turn-end background skill review."""

    enabled: bool = Field(
        default=False,
        description="Whether to run a daemon background review after enough tool-use turns.",
    )
    skill_nudge_interval: int = Field(
        default=10,
        ge=0,
        description="Number of new tool calls since the last skill write before triggering review. 0 disables nudging.",
    )
    max_messages: int = Field(
        default=24,
        ge=2,
        description="Maximum recent conversation messages passed to the background reviewer.",
    )
    review_model_name: str | None = Field(
        default=None,
        description="Optional model name for background review. Defaults to the active app model.",
    )
    notifications: str = Field(
        default="off",
        description="Notification mode for background review summaries: off, on, or verbose.",
    )
    max_actions_per_review: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum skill actions a single background review may apply.",
    )
    timeout_seconds: float = Field(
        default=120,
        ge=5,
        description="Wall-clock budget for a single background review, including model calls and action application.",
    )
    max_concurrent_reviews: int = Field(
        default=1,
        ge=1,
        le=4,
        description="Maximum background reviews running at once across all threads.",
    )


class CuratorConfig(BaseModel):
    """Configuration for long-running custom skill lifecycle maintenance."""

    enabled: bool = Field(
        default=False,
        description="Whether to schedule skill curator lifecycle checks.",
    )
    interval_hours: float = Field(
        default=168,
        ge=1,
        description="Minimum hours between curator runs. Values below 1 are rejected so the scheduler can never be always-due.",
    )
    min_idle_hours: float = Field(
        default=2,
        ge=0,
        description="Minimum agent idle time before curator may run.",
    )
    stale_after_days: int = Field(
        default=30,
        ge=1,
        description="Days without activity before a managed skill becomes stale.",
    )
    archive_after_days: int = Field(
        default=90,
        ge=1,
        description="Days without activity before a stale managed skill is archived.",
    )
    consolidate: bool = Field(
        default=False,
        description="Whether to run an optional LLM consolidation pass after deterministic lifecycle changes.",
    )
    model_name: str | None = Field(
        default=None,
        description="Optional model name for curator consolidation.",
    )

    @model_validator(mode="after")
    def _validate_lifecycle_windows(self) -> "CuratorConfig":
        if self.archive_after_days <= self.stale_after_days:
            raise ValueError("curator.archive_after_days must be greater than curator.stale_after_days.")
        return self


class SessionSearchConfig(BaseModel):
    """Configuration for SQLite FTS5 cross-session search."""

    enabled: bool = Field(
        default=True,
        description="Whether to index conversation turns and expose the session_search tool.",
    )
    db_path: str | None = Field(
        default=None,
        description="Optional SQLite database path. Relative paths resolve under AGENTFLOW_HOME.",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Default maximum number of session_search results.",
    )
    retention_days: int = Field(
        default=90,
        ge=1,
        description="Number of days to keep indexed session messages when auto_prune is enabled.",
    )
    auto_prune: bool = Field(
        default=False,
        description="Whether to soft-delete indexed messages older than retention_days after indexing.",
    )
    index_assistant: bool = Field(
        default=True,
        description="Whether to index final assistant replies in addition to user messages.",
    )
