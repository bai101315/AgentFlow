"""Usage and provenance metadata for custom skills."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from skill.manager import atomic_write, get_custom_skills_dir, validate_skill_name

USAGE_FILE_NAME = ".usage.json"
CURATOR_MANAGED_ORIGIN = "background_review"


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
        "created_by": "agent",
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


def update_skill_usage_for_write(name: str, *, action: str, origin: str) -> None:
    """Record skill write provenance after a successful skill_manage action."""
    name = validate_skill_name(name)
    now = utc_now_iso()
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
        record["created_by"] = "agent"

    origins = record.setdefault("origins", [])
    if origin not in origins:
        origins.append(origin)
    record["last_origin"] = origin
    record["last_activity_at"] = now

    if action in {"patch", "edit", "write_file", "remove_file"}:
        record["patch_count"] = int(record.get("patch_count") or 0) + 1

    if origin == CURATOR_MANAGED_ORIGIN:
        record["managed_by_curator"] = True

    if record.get("state") == "stale":
        record["state"] = "active"

    write_usage(data)


def record_skill_access(name: str, *, access: str) -> None:
    """Record a skill view/use signal for future curator decisions."""
    name = validate_skill_name(name)
    now = utc_now_iso()
    data = read_usage()
    skills = data.setdefault("skills", {})
    record = skills.get(name)
    if not isinstance(record, dict):
        record = _default_record(name, now=now)
        skills[name] = record

    if access == "view":
        record["view_count"] = int(record.get("view_count") or 0) + 1
    elif access == "use":
        record["use_count"] = int(record.get("use_count") or 0) + 1
    else:
        raise ValueError("access must be 'view' or 'use'.")

    record["last_activity_at"] = now
    if record.get("state") == "stale":
        record["state"] = "active"
    write_usage(data)


def is_curator_managed(record: dict[str, Any]) -> bool:
    if record.get("pinned"):
        return False
    if record.get("managed_by_curator") is True:
        return True
    origins = record.get("origins")
    return isinstance(origins, list) and CURATOR_MANAGED_ORIGIN in origins
