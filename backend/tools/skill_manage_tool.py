"""Tool for creating and evolving custom skills."""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from weakref import WeakValueDictionary

from agents.lead_agent.prompt import refresh_skills_system_prompt_cache_async
from agents.thread_state import ThreadState
from deer_flow_mcp.tools import _make_sync_tool_wrapper
from langchain.tools import ToolRuntime, tool
from langgraph.typing import ContextT
from skill.events import emit_event
from skill.manager import (
    append_history,
    atomic_write,
    custom_skill_exists,
    ensure_custom_skill_is_editable,
    ensure_safe_support_path,
    get_custom_skill_dir,
    get_custom_skill_file,
    public_skill_exists,
    read_custom_skill_content,
    validate_skill_markdown_content,
    validate_skill_name,
)
from skill.security_scanner import scan_skill_content
from skill.usage import (
    BACKGROUND_ORIGINS,
    ORIGIN_FOREGROUND_USER,
    VALID_ORIGINS,
    get_record,
    is_curator_managed,
    update_skill_usage_for_write,
)

logger = logging.getLogger(__name__)

# Execution contexts. ``foreground`` is a user-driven turn; anything else is an
# automated harness that must declare a background origin (§3.3).
EXECUTION_CONTEXT_FOREGROUND = "foreground"

# Whole-skill deletion is intentionally suspended. Archive is the only
# supported lifecycle action for removing a complete skill from the active set.
_SUSPENDED_ACTIONS = frozenset({"delete"})

# Actions that destroy content. Automated origins may never perform them: the
# most destructive automatic action allowed is archive (§3.5).
_DESTRUCTIVE_ACTIONS = frozenset({"delete", "remove_file"})


_skill_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()


def _get_lock(name: str) -> threading.Lock:
    lock = _skill_locks.get(name)
    if lock is None:
        lock = threading.Lock()
        _skill_locks[name] = lock
    return lock


def _get_thread_id(runtime: ToolRuntime[ContextT, ThreadState] | None) -> str | None:
    if runtime is None:
        return None
    if runtime.context and runtime.context.get("thread_id"):
        return runtime.context.get("thread_id")
    return runtime.config.get("configurable", {}).get("thread_id")


def _get_parent_thread_id(runtime: ToolRuntime[ContextT, ThreadState] | None) -> str | None:
    """Return the foreground thread a background write descends from, if any."""
    if runtime is None:
        return None
    if runtime.context and runtime.context.get("parent_thread_id"):
        return runtime.context.get("parent_thread_id")
    return runtime.config.get("configurable", {}).get("parent_thread_id")


def _history_record(
    *,
    action: str,
    file_path: str,
    prev_content: str | None,
    new_content: str | None,
    thread_id: str | None,
    scanner: dict[str, Any],
    origin: str = ORIGIN_FOREGROUND_USER,
    parent_thread_id: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "author": origin,
        "origin": origin,
        "thread_id": thread_id,
        "parent_thread_id": parent_thread_id,
        "agent_name": agent_name,
        "file_path": file_path,
        "prev_content": prev_content,
        "new_content": new_content,
        "scanner": scanner,
    }


def _enforce_write_policy(*, action: str, name: str, origin: str, execution_context: str) -> None:
    """Validate that *origin* is allowed to perform *action* on *name*.

    Implements the foreground/background permission split in §7.3:
    background origins may never delete, may never touch a pinned skill, and
    may only modify skills that are explicitly curator-managed — which keeps
    user-authored skills out of automated maintenance.
    """
    if origin not in VALID_ORIGINS:
        raise ValueError(f"Unknown skill write origin '{origin}'. Expected one of: {', '.join(sorted(VALID_ORIGINS))}.")

    if action in _SUSPENDED_ACTIONS:
        raise PermissionError(
            "Direct Skill deletion is temporarily disabled. Use patch/edit to revise the Skill, "
            "or archive it through the Curator when lifecycle governance is enabled."
        )

    if execution_context != EXECUTION_CONTEXT_FOREGROUND and origin not in BACKGROUND_ORIGINS:
        from config import get_app_config

        if getattr(get_app_config().skill_evolution, "require_background_provenance", True):
            raise PermissionError(
                f"Write from execution context '{execution_context}' must declare a background origin, got '{origin}'."
            )

    if origin not in BACKGROUND_ORIGINS:
        return

    if action in _DESTRUCTIVE_ACTIONS:
        raise PermissionError(f"Origin '{origin}' may not perform '{action}'; automated changes must be recoverable.")

    record = get_record(name)
    if record.get("pinned"):
        raise PermissionError(f"Custom skill '{name}' is pinned and cannot be modified by origin '{origin}'.")
    if action != "create" and not is_curator_managed(record):
        raise PermissionError(
            f"Custom skill '{name}' is not curator-managed, so origin '{origin}' may not modify it. "
            "Only skills created by an automated origin are eligible."
        )


def _finalize_write(
    *,
    name: str,
    action: str,
    origin: str,
    thread_id: str | None,
    file_path: str,
    prev_content: str | None,
    new_content: str | None,
    scanner: dict[str, Any],
    parent_thread_id: str | None = None,
    execution_context: str = EXECUTION_CONTEXT_FOREGROUND,
    agent_name: str | None = None,
    model_name: str | None = None,
) -> None:
    """Record history, usage provenance and an event for a completed write.

    The content write has already succeeded and is authoritative, so a
    bookkeeping failure is logged rather than raised: surfacing it would report
    the write as failed when it actually landed (§3.4).
    """
    try:
        append_history(
            name,
            _history_record(
                action=action,
                file_path=file_path,
                prev_content=prev_content,
                new_content=new_content,
                thread_id=thread_id,
                scanner=scanner,
                origin=origin,
                parent_thread_id=parent_thread_id,
                agent_name=agent_name,
            ),
        )
    except Exception:
        logger.warning("Failed to append history for skill '%s' action '%s'", name, action, exc_info=True)

    try:
        update_skill_usage_for_write(name, action=action, origin=origin)
    except Exception:
        logger.warning("Failed to record usage for skill '%s' action '%s'", name, action, exc_info=True)

    emit_event(
        f"skill_{action}",
        skill=name,
        action=action,
        origin=origin,
        execution_context=execution_context,
        thread_id=thread_id,
        parent_thread_id=parent_thread_id,
        agent_name=agent_name,
        model_name=model_name,
        file_path=file_path,
        scanner=scanner,
    )



async def _scan_or_raise(content: str, *, executable: bool, location: str) -> dict[str, str]:
    result = await scan_skill_content(content, executable=executable, location=location)
    if result.decision == "block":
        raise ValueError(f"Security scan blocked the write: {result.reason}")
    if executable and result.decision != "allow":
        raise ValueError(f"Security scan rejected executable content: {result.reason}")
    return {"decision": result.decision, "reason": result.reason}


async def _to_thread(func, /, *args, **kwargs):
    """Run a short Skill-store operation without crossing event-loop executors.

    Skill writes are already protected by a process-level lock. Using
    ``asyncio.to_thread`` here caused nested executor waits in the isolated
    review loop, where the executor may have only one worker. Keeping this
    adapter asynchronous preserves the existing call sites and avoids binding
    locks or filesystem work to another event loop.
    """
    return func(*args, **kwargs)


async def _skill_manage_impl(
    runtime: ToolRuntime[ContextT, ThreadState] | None,
    action: str,
    name: str,
    content: str | None = None,
    path: str | None = None,
    find: str | None = None,
    replace: str | None = None,
    expected_count: int | None = None,
    origin: str = ORIGIN_FOREGROUND_USER,
    execution_context: str = EXECUTION_CONTEXT_FOREGROUND,
    agent_name: str | None = None,
    model_name: str | None = None,
) -> str:
    """Manage custom skills under skills/custom/.

    Args:
        action: One of create, patch, edit, write_file, remove_file, archive, restore. Direct Skill deletion is disabled.
        name: Skill name in hyphen-case.
        content: New file content for create, edit, or write_file. Pass "force" for restore to overwrite existing.
        path: Supporting file path for write_file or remove_file.
        find: Existing text to replace for patch.
        replace: Replacement text for patch.
        expected_count: Optional expected number of replacements for patch.
        origin: Write provenance, one of foreground_user, background_review, curator, migration.
        execution_context: foreground for user-driven turns, otherwise the automated harness name.
    """
    name = validate_skill_name(name)
    lock = _get_lock(name)
    thread_id = _get_thread_id(runtime)
    parent_thread_id = _get_parent_thread_id(runtime)
    await _to_thread(
        _enforce_write_policy,
        action=action,
        name=name,
        origin=origin,
        execution_context=execution_context,
    )

    def finalize(**kwargs) -> None:
        _finalize_write(
            name=name,
            origin=origin,
            thread_id=thread_id,
            parent_thread_id=parent_thread_id,
            execution_context=execution_context,
            **kwargs,
        )

    # Acquire outside the executor. Submitting ``lock.acquire`` to the same
    # executor used by the protected filesystem calls can deadlock when the
    # executor has a single worker.
    lock.acquire()
    try:
        if action == "create":
            if await _to_thread(custom_skill_exists, name):
                raise ValueError(f"Custom skill '{name}' already exists.")
            if content is None:
                raise ValueError("content is required for create.")
            await _to_thread(validate_skill_markdown_content, name, content)
            scan = await _scan_or_raise(content, executable=False, location=f"{name}/SKILL.md")
            skill_file = await _to_thread(get_custom_skill_file, name)
            await _to_thread(atomic_write, skill_file, content)
            await _to_thread(
                finalize,
                action="create",
                file_path="SKILL.md",
                prev_content=None,
                new_content=content,
                scanner=scan,
                agent_name=agent_name,
                model_name=model_name,
            )
            try:
                await refresh_skills_system_prompt_cache_async()
            except Exception:
                logger.warning("Failed to refresh Skill prompt cache after '%s'", name, exc_info=True)
            return (
                f"Created custom skill '{name}'. "
                f"To add reference files, templates, or scripts, use action='write_file' with "
                f"path='references/example.md', 'templates/example', or 'scripts/example'."
            )

        if action == "edit":
            await _to_thread(ensure_custom_skill_is_editable, name)
            if content is None:
                raise ValueError("content is required for edit.")
            await _to_thread(validate_skill_markdown_content, name, content)
            scan = await _scan_or_raise(content, executable=False, location=f"{name}/SKILL.md")
            skill_file = await _to_thread(get_custom_skill_file, name)
            prev_content = await _to_thread(skill_file.read_text, encoding="utf-8")
            await _to_thread(atomic_write, skill_file, content)
            await _to_thread(
                finalize,
                action="edit",
                file_path="SKILL.md",
                prev_content=prev_content,
                new_content=content,
                scanner=scan,
                agent_name=agent_name,
                model_name=model_name,
            )
            try:
                await refresh_skills_system_prompt_cache_async()
            except Exception:
                logger.warning("Failed to refresh Skill prompt cache after '%s'", name, exc_info=True)
            return f"Updated custom skill '{name}'."

        if action == "patch":
            await _to_thread(ensure_custom_skill_is_editable, name)
            if find is None or replace is None:
                raise ValueError("find and replace are required for patch.")
            skill_file = await _to_thread(get_custom_skill_file, name)
            prev_content = await _to_thread(skill_file.read_text, encoding="utf-8")
            occurrences = prev_content.count(find)
            if occurrences == 0:
                raise ValueError("Patch target not found in SKILL.md.")
            if expected_count is not None and occurrences != expected_count:
                raise ValueError(f"Expected {expected_count} replacements but found {occurrences}.")
            replacement_count = expected_count if expected_count is not None else 1
            new_content = prev_content.replace(find, replace, replacement_count)
            await _to_thread(validate_skill_markdown_content, name, new_content)
            scan = await _scan_or_raise(new_content, executable=False, location=f"{name}/SKILL.md")
            await _to_thread(atomic_write, skill_file, new_content)
            await _to_thread(
                finalize,
                action="patch",
                file_path="SKILL.md",
                prev_content=prev_content,
                new_content=new_content,
                scanner=scan,
                agent_name=agent_name,
                model_name=model_name,
            )
            try:
                await refresh_skills_system_prompt_cache_async()
            except Exception:
                logger.warning("Failed to refresh Skill prompt cache after '%s'", name, exc_info=True)
            return f"Patched custom skill '{name}' ({replacement_count} replacement(s) applied, {occurrences} match(es) found)."

        if action == "delete":
            await _to_thread(ensure_custom_skill_is_editable, name)
            skill_dir = await _to_thread(get_custom_skill_dir, name)
            prev_content = await _to_thread(read_custom_skill_content, name)
            await _to_thread(shutil.rmtree, skill_dir)
            await _to_thread(
                finalize,
                action="delete",
                file_path="SKILL.md",
                prev_content=prev_content,
                new_content=None,
                scanner={"decision": "allow", "reason": "Deletion requested."},
            )
            try:
                await refresh_skills_system_prompt_cache_async()
            except Exception:
                logger.warning("Failed to refresh Skill prompt cache after '%s'", name, exc_info=True)
            return f"Deleted custom skill '{name}'."

        if action == "write_file":
            await _to_thread(ensure_custom_skill_is_editable, name)
            if path is None or content is None:
                raise ValueError("path and content are required for write_file.")
            target = await _to_thread(ensure_safe_support_path, name, path)
            exists = await _to_thread(target.exists)
            prev_content = await _to_thread(target.read_text, encoding="utf-8") if exists else None
            executable = "scripts/" in path or path.startswith("scripts/")
            scan = await _scan_or_raise(content, executable=executable, location=f"{name}/{path}")
            await _to_thread(atomic_write, target, content)
            await _to_thread(
                finalize,
                action="write_file",
                file_path=path,
                prev_content=prev_content,
                new_content=content,
                scanner=scan,
                agent_name=agent_name,
                model_name=model_name,
            )
            return f"Wrote '{path}' for custom skill '{name}'."

        if action == "remove_file":
            await _to_thread(ensure_custom_skill_is_editable, name)
            if path is None:
                raise ValueError("path is required for remove_file.")
            target = await _to_thread(ensure_safe_support_path, name, path)
            if not await _to_thread(target.exists):
                raise FileNotFoundError(f"Supporting file '{path}' not found for skill '{name}'.")
            prev_content = await _to_thread(target.read_text, encoding="utf-8")
            await _to_thread(target.unlink)
            await _to_thread(
                finalize,
                action="remove_file",
                file_path=path,
                prev_content=prev_content,
                new_content=None,
                scanner={"decision": "allow", "reason": "Deletion requested."},
                agent_name=agent_name,
                model_name=model_name,
            )
            return f"Removed '{path}' from custom skill '{name}'."

        if action == "archive":
            await _to_thread(ensure_custom_skill_is_editable, name)
            prev_content = await _to_thread(read_custom_skill_content, name)
            from skill.curator import archive_custom_skill, _mark_archived, _now as curator_now

            archive_path = await _to_thread(archive_custom_skill, name)
            await _to_thread(_mark_archived, name, archive_path, now=curator_now())
            await _to_thread(
                finalize,
                action="archive",
                file_path="SKILL.md",
                prev_content=prev_content,
                new_content=None,
                scanner={"decision": "allow", "reason": "User-initiated archive."},
                agent_name=agent_name,
                model_name=model_name,
            )
            try:
                await refresh_skills_system_prompt_cache_async()
            except Exception:
                logger.warning("Failed to refresh Skill prompt cache after archive '%s'", name, exc_info=True)
            return f"Archived custom skill '{name}' to {archive_path}."

        if action == "restore":
            from skill.curator import restore_archived_skill

            force = content == "force"
            restored_path = await _to_thread(restore_archived_skill, name, force=force)
            await _to_thread(
                finalize,
                action="restore",
                file_path="SKILL.md",
                prev_content=None,
                new_content=None,
                scanner={"decision": "allow", "reason": "User-initiated restore."},
                agent_name=agent_name,
                model_name=model_name,
            )
            try:
                await refresh_skills_system_prompt_cache_async()
            except Exception:
                logger.warning("Failed to refresh Skill prompt cache after restore '%s'", name, exc_info=True)
            return f"Restored custom skill '{name}' to {restored_path}."

        if await _to_thread(public_skill_exists, name):
            raise ValueError(f"'{name}' is a built-in skill. To customise it, create a new skill with the same name under skills/custom/.")
        raise ValueError(f"Unsupported action '{action}'.")

    finally:
        lock.release()


@tool("skill_manage", parse_docstring=True)
async def skill_manage_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    action: str,
    name: str,
    content: str | None = None,
    path: str | None = None,
    find: str | None = None,
    replace: str | None = None,
    expected_count: int | None = None,
) -> str:
    """Manage custom skills under skills/custom/.

    Args:
        action: One of create, patch, edit, write_file, remove_file, archive, restore. Direct Skill deletion is disabled.
        name: Skill name in hyphen-case.
        content: New file content for create, edit, or write_file. Pass "force" for restore to overwrite existing.
        path: Supporting file path for write_file or remove_file.
        find: Existing text to replace for patch.
        replace: Replacement text for patch.
        expected_count: Optional expected number of replacements for patch.
    """
    return await _skill_manage_impl(
        runtime=runtime,
        action=action,
        name=name,
        content=content,
        path=path,
        find=find,
        replace=replace,
        expected_count=expected_count,
    )


skill_manage_tool.func = _make_sync_tool_wrapper(_skill_manage_impl, "skill_manage")


def build_background_skill_manage_tool(
    *,
    origin: str,
    execution_context: str,
    thread_id: str | None,
    parent_thread_id: str | None,
    agent_name: str | None = None,
    model_name: str | None = None,
    max_actions: int | None = None,
    on_applied: Callable[[str], None] | None = None,
):
    """Build a ``skill_manage`` tool bound to a background origin.

    The isolated review runtime gets this instead of the foreground tool so it
    cannot claim foreground provenance, and so its destructive actions are
    rejected by the write policy rather than merely discouraged by a prompt.
    ``delete``/``remove_file`` are not even exposed in the schema.
    """

    successful_writes = 0

    @tool("skill_manage", parse_docstring=True)
    async def background_skill_manage(
        action: str,
        name: str,
        content: str | None = None,
        path: str | None = None,
        find: str | None = None,
        replace: str | None = None,
        expected_count: int | None = None,
    ) -> str:
        """Create or improve a custom skill. Deleting skills is not permitted here.

        Args:
            action: One of create, patch, edit, write_file.
            name: Skill name in hyphen-case.
            content: New file content for create, edit, or write_file.
            path: Supporting file path under references/, templates/, scripts/, or assets/ for write_file.
            find: Existing text to replace for patch.
            replace: Replacement text for patch.
            expected_count: Optional expected number of replacements for patch.
        """
        runtime = SimpleNamespace(
            context={"thread_id": thread_id, "parent_thread_id": parent_thread_id},
            config={"configurable": {"thread_id": thread_id, "parent_thread_id": parent_thread_id}},
        )
        nonlocal successful_writes
        if max_actions is not None and successful_writes >= max_actions:
            raise PermissionError(f"Background review action limit reached ({max_actions}).")

        result = await _skill_manage_impl(
            runtime=runtime,
            action=action,
            name=name,
            content=content,
            path=path,
            find=find,
            replace=replace,
            expected_count=expected_count,
            origin=origin,
            execution_context=execution_context,
            agent_name=agent_name,
            model_name=model_name,
        )
        successful_writes += 1
        if on_applied is not None:
            on_applied(result)
        return result

    return background_skill_manage
