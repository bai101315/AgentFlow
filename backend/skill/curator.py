"""Deterministic lifecycle maintenance for custom skills."""

from __future__ import annotations

import json
import logging
import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from config.self_improvement_config import CuratorConfig
from skill.action_parsing import parse_actions_json
from skill.manager import get_custom_skill_dir, get_custom_skills_dir, validate_skill_name
from skill.usage import is_curator_managed, read_usage, write_usage

logger = logging.getLogger(__name__)

ARCHIVE_DIR_NAME = ".archive"
CURATOR_STATE_FILE_NAME = ".curator_state.json"

_CONSOLIDATION_PROMPT = """You are a skill curator.
Review only the provided custom skills. Suggest JSON actions to merge narrow or overlapping background-created skills into broader umbrella skills.
Never delete skills. Use archive_skill for old narrow skills after their useful content has been moved.
Allowed actions:
- patch_skill: {"action":"patch_skill","name":"umbrella","find":"exact text","replace":"replacement","expected_count":1}
- create_skill: {"action":"create_skill","name":"class-level-name","content":"full SKILL.md"}
- write_support_file: {"action":"write_support_file","name":"umbrella","path":"references/detail.md","content":"..."}
- archive_skill: {"action":"archive_skill","name":"old-narrow-skill","reason":"merged into umbrella"}
- noop: {"action":"noop","reason":"..."}
Return strict JSON: {"actions":[...]}.
"""


def get_archive_dir() -> Path:
    path = get_custom_skills_dir() / ARCHIVE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_curator_state_file() -> Path:
    return get_custom_skills_dir() / CURATOR_STATE_FILE_NAME


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _archive_target(name: str) -> Path:
    name = validate_skill_name(name)
    root = get_archive_dir()
    target = root / name
    if not target.exists():
        return target
    suffix = _now().strftime("%Y%m%d%H%M%S")
    return root / f"{name}-{suffix}"


def archive_custom_skill(name: str) -> Path:
    """Move a custom skill directory into skills/custom/.archive/."""
    name = validate_skill_name(name)
    source = get_custom_skill_dir(name)
    if not source.exists():
        raise FileNotFoundError(f"Custom skill '{name}' not found.")
    target = _archive_target(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return target


def _mark_archived(name: str, archive_path: Path, *, now: datetime) -> None:
    usage = read_usage()
    record = usage.setdefault("skills", {}).setdefault(name, {})
    record["state"] = "archived"
    record["archived_at"] = now.isoformat()
    record["archive_path"] = str(archive_path)
    write_usage(usage)


def apply_automatic_transitions(config: CuratorConfig, *, now: datetime | None = None) -> dict[str, Any]:
    """Apply active -> stale -> archived transitions without using an LLM."""
    now = now or _now()
    usage = read_usage()
    skills = usage.setdefault("skills", {})
    stale_cutoff = now - timedelta(days=config.stale_after_days)
    archive_cutoff = now - timedelta(days=config.archive_after_days)

    changed: list[dict[str, Any]] = []
    archived: list[dict[str, Any]] = []

    for name, record in list(skills.items()):
        if not isinstance(record, dict):
            continue
        try:
            validate_skill_name(name)
        except ValueError:
            continue
        if not is_curator_managed(record):
            continue

        state = record.get("state") or "active"
        last_activity = _parse_dt(record.get("last_activity_at")) or _parse_dt(record.get("created_at")) or now

        if state == "active" and last_activity <= stale_cutoff:
            record["state"] = "stale"
            changed.append({"name": name, "from": "active", "to": "stale"})
            state = "stale"

        if state == "stale" and last_activity <= archive_cutoff:
            try:
                archive_path = archive_custom_skill(name)
            except FileNotFoundError:
                archive_path = get_archive_dir() / name
            record["state"] = "archived"
            record["archived_at"] = now.isoformat()
            record["archive_path"] = str(archive_path)
            archived.append({"name": name, "path": str(archive_path)})

    if changed or archived:
        write_usage(usage)

    return {"staled": changed, "archived": archived}


def read_curator_state() -> dict[str, Any]:
    path = get_curator_state_file()
    if not path.exists():
        return {"version": 1, "last_run_at": None, "run_count": 0, "paused": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "last_run_at": None, "run_count": 0, "paused": False}
    if not isinstance(data, dict):
        return {"version": 1, "last_run_at": None, "run_count": 0, "paused": False}
    data.setdefault("version", 1)
    data.setdefault("run_count", 0)
    data.setdefault("paused", False)
    return data


def write_curator_state(state: dict[str, Any]) -> None:
    path = get_curator_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def collect_consolidation_candidates() -> list[dict[str, Any]]:
    usage = read_usage()
    candidates: list[dict[str, Any]] = []
    for name, record in usage.get("skills", {}).items():
        if not isinstance(record, dict) or record.get("state") == "archived":
            continue
        if not is_curator_managed(record):
            continue
        skill_file = get_custom_skill_dir(name) / "SKILL.md"
        if not skill_file.exists():
            continue
        candidates.append(
            {
                "name": name,
                "state": record.get("state", "active"),
                "patch_count": record.get("patch_count", 0),
                "use_count": record.get("use_count", 0),
                "last_activity_at": record.get("last_activity_at"),
                "content": skill_file.read_text(encoding="utf-8"),
            }
        )
    return candidates


def _model_consolidation_actions(config: CuratorConfig, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from langchain_core.messages import HumanMessage, SystemMessage
    from models import create_chat_model

    model = create_chat_model(name=config.model_name, thinking_enabled=False)
    response = model.invoke(
        [
            SystemMessage(content=_CONSOLIDATION_PROMPT),
            HumanMessage(content=json.dumps({"skills": candidates}, ensure_ascii=False)),
        ]
    )
    return parse_actions_json(str(getattr(response, "content", "")))


async def apply_consolidation_actions(actions: list[dict[str, Any]], *, max_actions: int = 8) -> list[str]:
    from agents.middlewares.background_review_middleware import BACKGROUND_REVIEW_ORIGIN
    from tools.skill_manage_tool import _skill_manage_impl

    runtime = SimpleNamespace(context={}, config={"configurable": {}})
    summaries: list[str] = []
    for action in actions[:max_actions]:
        name = str(action.get("name") or "")
        action_name = action.get("action")
        try:
            if action_name == "patch_skill":
                result = await _skill_manage_impl(
                    runtime=runtime,
                    action="patch",
                    name=name,
                    find=action.get("find"),
                    replace=action.get("replace"),
                    expected_count=action.get("expected_count"),
                    origin=BACKGROUND_REVIEW_ORIGIN,
                )
                summaries.append(result)
            elif action_name == "create_skill":
                result = await _skill_manage_impl(
                    runtime=runtime,
                    action="create",
                    name=name,
                    content=action.get("content"),
                    origin=BACKGROUND_REVIEW_ORIGIN,
                )
                summaries.append(result)
            elif action_name == "write_support_file":
                result = await _skill_manage_impl(
                    runtime=runtime,
                    action="write_file",
                    name=name,
                    path=action.get("path"),
                    content=action.get("content"),
                    origin=BACKGROUND_REVIEW_ORIGIN,
                )
                summaries.append(result)
            elif action_name == "archive_skill":
                usage = read_usage()
                record = usage.get("skills", {}).get(name)
                if not isinstance(record, dict) or not is_curator_managed(record):
                    continue
                archive_path = archive_custom_skill(name)
                _mark_archived(name, archive_path, now=_now())
                summaries.append(f"Archived custom skill '{name}'.")
        except Exception as exc:
            logger.warning("Curator consolidation action failed: %s", exc, exc_info=True)
    return summaries


def run_curator_consolidation(
    config: CuratorConfig,
    *,
    reviewer: Callable[[list[dict[str, Any]], CuratorConfig], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    candidates = collect_consolidation_candidates()
    if not candidates:
        return {"candidates": 0, "actions": []}
    actions = reviewer(candidates, config) if reviewer else _model_consolidation_actions(config, candidates)
    summaries = asyncio.run(apply_consolidation_actions(actions))
    return {"candidates": len(candidates), "actions": summaries}


def should_run_curator(config: CuratorConfig, *, idle_for_seconds: float | None = None, now: datetime | None = None) -> bool:
    if not config.enabled:
        return False
    state = read_curator_state()
    if state.get("paused"):
        return False
    min_idle_seconds = config.min_idle_hours * 3600
    if idle_for_seconds is not None and idle_for_seconds < min_idle_seconds:
        return False
    last_run_at = _parse_dt(state.get("last_run_at"))
    if last_run_at is None:
        state["last_run_at"] = (now or _now()).isoformat()
        write_curator_state(state)
        return False
    return ((now or _now()) - last_run_at).total_seconds() >= config.interval_hours * 3600


def run_curator(
    config: CuratorConfig,
    *,
    now: datetime | None = None,
    consolidation_reviewer: Callable[[list[dict[str, Any]], CuratorConfig], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    now = now or _now()
    summary = apply_automatic_transitions(config, now=now)
    if config.consolidate:
        summary["consolidation"] = run_curator_consolidation(config, reviewer=consolidation_reviewer)

    state = read_curator_state()
    state["last_run_at"] = now.isoformat()
    state["run_count"] = int(state.get("run_count") or 0) + 1
    state["last_run_summary"] = summary
    write_curator_state(state)
    return summary
