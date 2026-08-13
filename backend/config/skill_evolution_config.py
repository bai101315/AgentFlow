from pydantic import BaseModel, Field


class SkillEvolutionConfig(BaseModel):
    """Configuration for agent-managed skill evolution."""

    enabled: bool = Field(
        default=False,
        description="Whether the agent can create and modify skills under skills/custom.",
    )
    moderation_model_name: str | None = Field(
        default=None,
        description="Optional model name for skill security moderation. Defaults to the primary chat model.",
    )
    auto_create: bool = Field(
        default=True,
        description="Whether the agent may create new custom skills without confirmation when the reusable pattern is clear.",
    )
    require_background_provenance: bool = Field(
        default=True,
        description="Whether background writes must carry an explicit background origin. When True, a background write with a foreground origin is rejected.",
    )
