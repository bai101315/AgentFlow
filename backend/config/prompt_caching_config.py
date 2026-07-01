"""Configuration for prompt/context caching behavior."""

from pydantic import BaseModel, Field


class PromptCachingConfig(BaseModel):
    """Prompt caching configuration."""

    enabled: bool = Field(default=True, description="Enable one frozen session prompt per thread")
    log_usage: bool = Field(default=True, description="Log provider cache hit/miss token usage")


_prompt_caching_config: PromptCachingConfig = PromptCachingConfig()


def get_prompt_caching_config() -> PromptCachingConfig:
    """Get the current prompt caching configuration."""
    return _prompt_caching_config


def set_prompt_caching_config(config: PromptCachingConfig) -> None:
    """Set the current prompt caching configuration."""
    global _prompt_caching_config
    _prompt_caching_config = config


def load_prompt_caching_config_from_dict(config_dict: dict) -> None:
    """Load prompt caching configuration from a dictionary."""
    global _prompt_caching_config
    _prompt_caching_config = PromptCachingConfig(**(config_dict or {}))
