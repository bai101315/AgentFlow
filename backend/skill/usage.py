"""Usage and provenance metadata for custom skills.

Provenance rules (docs/SELF_IMPROVING_MIGRATION.md §7.2):

``created_by`` records the origin of the *first* write that created the skill
and is never overwritten by later patches.  Curator eligibility is a separate,
explicit ``managed_by_curator`` flag that is only set when the skill is
*created* by a background origin.  Keeping the two apart is what stops a
background patch of a user-authored skill from silently enrolling that skill
into automatic archival.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from skill.manager import atomic_write, get_custom_skills_dir, validate_skill_name

USAGE_FILE_NAME = ".usage.json"

# Write origins. ``foreground_user`` means the user explicitly asked for the
# change in their own session; the background origins are automated.
ORIGIN_FOREGROUND_USER = "foreground_user"
ORIGIN_BACKGROUND_REVIEW = "background_review"
ORIGIN_CURATOR = "curator"
ORIGIN_MIGRATION = "migration"

VALID_ORIGINS = frozenset(
    {ORIGIN_FOREGROUND_USER, ORIGIN_BACKGROUND_REVIEW, ORIGIN_CURATOR, ORIGIN_MIGRATION}
)

# Origins that run without the user in the loop. These are subject to the
# restricted write policy in ``tools.skill_manage_tool``.
BACKGROUND_ORIGINS = frozenset({ORIGIN_BACKGROUND_REVIEW, ORIGIN_CURATOR})

# Origins whose *created* skills are eligible for automatic curator lifecycle
# management. ``migration`` is deliberately excluded: §7.2 requires curator
# enrolment for migrated skills to be specified explicitly.
CURATOR_MANAGED_ORIGINS = frozenset({ORIGIN_BACKGROUND_REVIEW, ORIGIN_CURATOR})

# Legacy marker: records written before explicit provenance existed used
# ``created_by="agent"`` to mean "curator-managed" (matching Hermes'
# tools/skill_usage.py). Preserved for backward compatibility on read.
_LEGACY_MANAGED_MARKER = "agent"

# ``.usage.json`` is a read-modify-write file touched from the foreground tool
# call, the background review thread and the curator timer, so serialise them.
_usage_lock = threading.RLock()


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_usage_file() -> Path:
    return get_custom_skills_dir() / USAGE_FILE_NAME


def read_usage() -> dict[str, Any]:
    path = get_usage_file()
    if not path.exists():
        return {"version": 1, "skills": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "skills": {}}
    if not isinstance(data, dict):
        return {"version": 1, "skills": {}}
    data.setdefault("version", 1)
    if not isinstance(data.get("skills"), dict):
        data["skills"] = {}
    return data


def write_usage(data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_write(get_usage_file(), payload + "\n")


def _default_record(name: str, *, now: str) -> dict[str, Any]:
    return {
        "name": name,
        "created_at": now,
        # Unknown until a create action records it. Never guessed from a patch:
        # an unknown author must not be treated as agent-authored.
        "created_by": None,
        "origins": [],
        "last_origin": None,
        "last_activity_at": now,
        "patch_count": 0,
        "use_count": 0,
        "view_count": 0,
        "state": "active",
        "pinned": False,
        "managed_by_curator": False,
    }


def get_record(name: str) -> dict[str, Any]:
    """Return the stored record for *name*, or a fresh default (not persisted)."""
    name = validate_skill_name(name)
    record = read_usage().get("skills", {}).get(name)
    if isinstance(record, dict):
        return record
    return _default_record(name, now=utc_now_iso())


def update_skill_usage_for_write(name: str, *, action: str, origin: str) -> None:
    """Record skill write provenance after a successful skill_manage action."""
    name = validate_skill_name(name)
    if origin not in VALID_ORIGINS:
        raise ValueError(f"Unknown skill write origin '{origin}'. Expected one of: {', '.join(sorted(VALID_ORIGINS))}.")
    now = utc_now_iso()

    with _usage_lock:
        data = read_usage()
        skills = data.setdefault("skills", {})

        if action == "delete":
            skills.pop(name, None)
            write_usage(data)
            return

        record = skills.get(name)
        if not isinstance(record, dict):
            record = _default_record(name, now=now)
            skills[name] = record

        if action == "create":
            record.setdefault("created_at", now)
            # Authorship is a historical fact fixed at creation time (§7.2).
            record["created_by"] = origin
            # Only skills *created* by an automated origin become curator
            # managed. A background patch of a user skill must not enrol it.
            record["managed_by_curator"] = origin in CURATOR_MANAGED_ORIGINS

        origins = record.setdefault("origins", [])
        if origin not in origins:
            origins.append(origin)
        record["last_origin"] = origin
        record["last_activity_at"] = now

        if action in {"patch", "edit", "write_file", "remove_file"}:
            record["patch_count"] = int(record.get("patch_count") or 0) + 1

        if record.get("state") == "stale":
            record["state"] = "active"

        write_usage(data)


def record_skill_access(name: str, *, access: str) -> None:
    """Record a skill view/use signal for future curator decisions."""
    name = validate_skill_name(name)
    if access not in {"view", "use"}:
        raise ValueError("access must be 'view' or 'use'.")
    now = utc_now_iso()

    with _usage_lock:
        data = read_usage()
        skills = data.setdefault("skills", {})
        record = skills.get(name)
        if not isinstance(record, dict):
            record = _default_record(name, now=now)
            skills[name] = record

        if access == "view":
            record["view_count"] = int(record.get("view_count") or 0) + 1
        else:
            record["use_count"] = int(record.get("use_count") or 0) + 1

        record["last_activity_at"] = now
        if record.get("state") == "stale":
            record["state"] = "active"
        write_usage(data)


def is_curator_managed(record: dict[str, Any]) -> bool:
    """Whether the curator may apply automatic lifecycle actions to a skill.

    Only the explicit ``managed_by_curator`` flag (or the legacy
    ``created_by="agent"`` marker) grants eligibility. The ``origins`` list is
    deliberately *not* consulted: it accumulates every write origin, so a
    single background patch would otherwise enrol a user-authored skill.
    """
    if record.get("pinned"):
        return False
    if record.get("managed_by_curator") is True:
        return True
    return record.get("created_by") == _LEGACY_MANAGED_MARKER
