"""Built-in tools for progressive skill disclosure.

Skills are disclosed in two steps (docs/SELF_IMPROVING_MIGRATION.md §5.3):

``skills_list`` returns metadata only — never skill bodies — so the index stays
cheap.  ``skill_view`` returns one skill's SKILL.md and the paths of its
support files, and records a ``view`` signal.  Actually reading a support file
goes through the normal file-read path, which records ``use``.  That split is
what lets the curator tell "the agent glanced at this skill" apart from "the
agent executed this workflow".
"""

from __future__ import annotations

import json
import logging

from config import get_app_config
from langchain_core.tools import tool
from skill.loader import load_skills
from skill.manager import ALLOWED_SUPPORT_SUBDIRS

logger = logging.getLogger(__name__)


def _record_access(name: str, access: str) -> None:
    """Record a view/use signal. Never breaks the caller (§3.4)."""
    try:
        from skill.usage import record_skill_access

        record_skill_access(name, access=access)
    except Exception:
        logger.debug("Failed to record skill %s for %s", access, name, exc_info=True)


def _container_location(skill) -> str:
    container_base = get_app_config().skills.container_path
    return skill.get_container_path(container_base)


@tool("skills_list", parse_docstring=True)
def skills_list_tool(category: str | None = None) -> str:
    """List available skills with metadata only, without loading their contents.

    Args:
        category: Optional filter, either public or custom. Omit to list both.
    """
    if category is not None and category not in {"public", "custom"}:
        return json.dumps({"error": "category must be 'public' or 'custom'."}, ensure_ascii=False)

    skills = [
        {
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "location": _container_location(skill),
        }
        for skill in load_skills(enabled_only=True)
        if category is None or skill.category == category
    ]
    return json.dumps({"skills": skills, "count": len(skills)}, ensure_ascii=False, indent=2)


@tool("skill_view", parse_docstring=True)
def skill_view_tool(name: str) -> str:
    """Read one skill's instructions and list its support files.

    Args:
        name: Skill name as reported by skills_list.
    """
    match = next((skill for skill in load_skills(enabled_only=True) if skill.name == name), None)
    if match is None:
        return json.dumps({"error": f"Skill '{name}' not found. Use skills_list to see available skills."}, ensure_ascii=False)

    try:
        content = match.skill_file.read_text(encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"Failed to read skill '{name}': {exc}"}, ensure_ascii=False)

    location = _container_location(match)
    support_files: list[str] = []
    for subdir in sorted(ALLOWED_SUPPORT_SUBDIRS):
        directory = match.skill_dir / subdir
        if not directory.is_dir():
            continue
        for entry in sorted(directory.rglob("*")):
            if entry.is_file() and not entry.name.startswith("."):
                relative = entry.relative_to(match.skill_dir).as_posix()
                support_files.append(f"{location}/{relative}")

    _record_access(name, "view")

    return json.dumps(
        {
            "name": match.name,
            "category": match.category,
            "location": location,
            "content": content,
            "support_files": support_files,
        },
        ensure_ascii=False,
        indent=2,
    )
