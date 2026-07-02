from pydantic import BaseModel, Field


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


class CuratorConfig(BaseModel):
    """Configuration for long-running custom skill lifecycle maintenance."""

    enabled: bool = Field(
        default=False,
        description="Whether to schedule skill curator lifecycle checks.",
    )
    interval_hours: float = Field(
        default=168,
        ge=0,
        description="Minimum hours between curator runs.",
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


class SessionSearchConfig(BaseModel):
    """Configuration for SQLite FTS5 cross-session search."""

    enabled: bool = Field(
        default=False,
        description="Whether to index conversation turns and expose the session_search tool.",
    )
    db_path: str | None = Field(
        default=None,
        description="Optional SQLite database path. Relative paths resolve under DEER_FLOW_HOME.",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Default maximum number of session_search results.",
    )
