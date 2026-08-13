from pathlib import Path

from pydantic import BaseModel, Field


def _default_repo_root() -> Path:
    """Resolve the repo root without relying on the current working directory.

    This file lives at ``backend/config/skills_config.py``, so the repo root is
    two levels up.  ``skill/loader.py`` already carries the same correction.
    """
    return Path(__file__).resolve().parents[2]


class SkillsConfig(BaseModel):
    """Configuration for skills system"""

    path: str | None = Field(
        default=None,
        description="Path to skills directory. If not specified, defaults to ../skills relative to backend directory",
    )
    container_path: str = Field(
        default="/mnt/skills",
        description="Path where skills are mounted in the sandbox container",
    )
    guard_agent_created: bool = Field(
        default=False,
        description=(
            "Whether to run an LLM security scan on agent-created skill writes. "
            "Off by default: the agent can already execute the same code paths via "
            "bash, so the scan adds friction without meaningful security. Enable "
            "for belt-and-suspenders moderation."
        ),
    )

    def get_skills_path(self) -> Path:
        """
        Get the resolved skills directory path.

        Returns:
            Path to the skills directory
        """
        if self.path:
            # Use configured path (can be absolute or relative)
            path = Path(self.path)
            if not path.is_absolute():
                # If relative, resolve from the repo root for deterministic behavior.
                path = _default_repo_root() / path
            return path.resolve()
        else:
            # Default: ../skills relative to backend directory
            from skill.loader import get_skills_root_path

            return get_skills_root_path()

    def get_skill_container_path(self, skill_name: str, category: str = "public") -> str:
        """
        Get the full container path for a specific skill.

        Args:
            skill_name: Name of the skill (directory name)
            category: Category of the skill (public or custom)

        Returns:
            Full path to the skill in the container
        """
        return f"{self.container_path}/{category}/{skill_name}"
