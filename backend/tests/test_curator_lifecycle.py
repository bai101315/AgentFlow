"""Tests for Curator lifecycle: backup, archive, restore, and pinned protection."""

from __future__ import annotations

import json
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from skill.curator import (
    archive_custom_skill,
    backup_skill,
    get_backup_dir,
    list_archived_skills,
    restore_archived_skill,
    _mark_archived,
    apply_automatic_transitions,
)


@pytest.fixture()
def skills_dir(tmp_path, monkeypatch):
    """Set up a temporary skills/custom/ directory structure."""
    custom_dir = tmp_path / "skills" / "custom"
    custom_dir.mkdir(parents=True)
    monkeypatch.setattr("skill.manager.get_custom_skills_dir", lambda: custom_dir)
    monkeypatch.setattr("skill.curator.get_custom_skills_dir", lambda: custom_dir)

    usage_file = custom_dir / ".usage.json"
    usage_file.write_text(json.dumps({"version": 1, "skills": {}}), encoding="utf-8")
    monkeypatch.setattr("skill.usage.get_usage_file", lambda: usage_file)

    return custom_dir


def _create_skill(skills_dir: Path, name: str, content: str = "---\nname: {name}\n---\n# Test") -> Path:
    """Helper to create a skill directory with a SKILL.md."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content.format(name=name), encoding="utf-8")
    return skill_dir


def _create_skill_with_support(skills_dir: Path, name: str) -> Path:
    """Create a skill with support files."""
    skill_dir = _create_skill(skills_dir, name)
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "notes.md").write_text("some notes", encoding="utf-8")
    scripts = skill_dir / "scripts"
    scripts.mkdir()
    (scripts / "run.sh").write_text("#!/bin/bash\necho hi", encoding="utf-8")
    return skill_dir


def _set_usage(skills_dir: Path, name: str, **kwargs) -> None:
    """Set usage record for a skill."""
    usage_file = skills_dir / ".usage.json"
    data = json.loads(usage_file.read_text(encoding="utf-8"))
    data.setdefault("skills", {})[name] = kwargs
    usage_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Backup tests ────────────────────────────────────────────────────────


class TestBackup:
    def test_backup_creates_tarball(self, skills_dir):
        _create_skill(skills_dir, "my-skill")
        result = backup_skill("my-skill")
        assert result.exists()
        assert result.suffix == ".gz"
        assert "my-skill" in result.name

    def test_backup_contains_all_files(self, skills_dir):
        _create_skill_with_support(skills_dir, "my-skill")
        result = backup_skill("my-skill")
        with tarfile.open(result, "r:gz") as tar:
            names = tar.getnames()
        assert "my-skill/SKILL.md" in names
        assert "my-skill/references/notes.md" in names
        assert "my-skill/scripts/run.sh" in names

    def test_backup_raises_on_missing_skill(self, skills_dir):
        with pytest.raises(FileNotFoundError, match="Cannot backup"):
            backup_skill("nonexistent")

    def test_backup_dir_created_under_archive(self, skills_dir):
        _create_skill(skills_dir, "my-skill")
        backup_skill("my-skill")
        backup_dir = get_backup_dir()
        assert backup_dir.exists()
        assert backup_dir.name == ".backups"
        assert backup_dir.parent.name == ".archive"


# ── Archive tests ───────────────────────────────────────────────────────


class TestArchive:
    def test_archive_creates_backup_then_moves(self, skills_dir):
        _create_skill(skills_dir, "my-skill")
        archive_path = archive_custom_skill("my-skill")
        assert archive_path.exists()
        assert not (skills_dir / "my-skill").exists()
        # Backup was created
        backups = list(get_backup_dir().glob("my-skill-*.tar.gz"))
        assert len(backups) == 1

    def test_archive_skip_backup(self, skills_dir):
        _create_skill(skills_dir, "my-skill")
        archive_path = archive_custom_skill("my-skill", skip_backup=True)
        assert archive_path.exists()
        backups = list(get_backup_dir().glob("my-skill-*.tar.gz"))
        assert len(backups) == 0

    def test_backup_failure_blocks_archive(self, skills_dir, monkeypatch):
        _create_skill(skills_dir, "my-skill")
        monkeypatch.setattr("skill.curator.backup_skill", lambda name: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            archive_custom_skill("my-skill")
        # Skill should still be in place
        assert (skills_dir / "my-skill" / "SKILL.md").exists()

    def test_archive_raises_on_missing_skill(self, skills_dir):
        with pytest.raises(FileNotFoundError):
            archive_custom_skill("nonexistent")


# ── Restore tests ───────────────────────────────────────────────────────


class TestRestore:
    def test_restore_moves_skill_back(self, skills_dir):
        _create_skill(skills_dir, "my-skill")
        archive_path = archive_custom_skill("my-skill", skip_backup=True)
        _set_usage(skills_dir, "my-skill", state="archived", archive_path=str(archive_path))

        result = restore_archived_skill("my-skill")
        assert result.exists()
        assert (result / "SKILL.md").exists()
        assert not archive_path.exists()

    def test_restore_updates_usage_metadata(self, skills_dir):
        _create_skill(skills_dir, "my-skill")
        archive_path = archive_custom_skill("my-skill", skip_backup=True)
        _set_usage(skills_dir, "my-skill", state="archived", archive_path=str(archive_path))

        restore_archived_skill("my-skill")

        usage_file = skills_dir / ".usage.json"
        data = json.loads(usage_file.read_text(encoding="utf-8"))
        record = data["skills"]["my-skill"]
        assert record["state"] == "active"
        assert "archived_at" not in record
        assert "archive_path" not in record

    def test_restore_raises_on_name_conflict(self, skills_dir):
        _create_skill(skills_dir, "my-skill")
        # Create an archived version manually
        archive_dir = skills_dir / ".archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived = archive_dir / "my-skill"
        archived.mkdir()
        (archived / "SKILL.md").write_text("archived", encoding="utf-8")
        _set_usage(skills_dir, "my-skill", state="archived", archive_path=str(archived))

        with pytest.raises(ValueError, match="already exists"):
            restore_archived_skill("my-skill")

    def test_restore_force_overwrites_existing(self, skills_dir):
        _create_skill(skills_dir, "my-skill")
        # Create archived version
        archive_dir = skills_dir / ".archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived = archive_dir / "my-skill"
        archived.mkdir()
        (archived / "SKILL.md").write_text("---\nname: my-skill\n---\n# Restored version", encoding="utf-8")
        _set_usage(skills_dir, "my-skill", state="archived", archive_path=str(archived))

        result = restore_archived_skill("my-skill", force=True)
        content = (result / "SKILL.md").read_text(encoding="utf-8")
        assert "Restored version" in content
        # Old version was backed up
        backups = list(get_backup_dir().glob("my-skill-*.tar.gz"))
        assert len(backups) == 1

    def test_restore_finds_timestamped_archive(self, skills_dir):
        archive_dir = skills_dir / ".archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamped = archive_dir / "my-skill-20260101120000"
        timestamped.mkdir()
        (timestamped / "SKILL.md").write_text("old", encoding="utf-8")
        _set_usage(skills_dir, "my-skill", state="archived", archive_path="/nonexistent")

        result = restore_archived_skill("my-skill")
        assert result.exists()

    def test_restore_raises_when_no_archive_found(self, skills_dir):
        with pytest.raises(FileNotFoundError, match="No archived version"):
            restore_archived_skill("my-skill")


# ── List archived tests ─────────────────────────────────────────────────


class TestListArchived:
    def test_lists_tracked_archived_skills(self, skills_dir):
        _set_usage(skills_dir, "archived-skill", state="archived", archived_at="2026-01-01", archive_path="/tmp/x")
        result = list_archived_skills()
        assert any(r["name"] == "archived-skill" for r in result)

    def test_lists_orphaned_archive_dirs(self, skills_dir):
        archive_dir = skills_dir / ".archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        orphan = archive_dir / "orphan-skill"
        orphan.mkdir()
        (orphan / "SKILL.md").write_text("hi", encoding="utf-8")

        result = list_archived_skills()
        assert any(r["name"] == "orphan-skill" for r in result)


# ── Automatic transitions with backup ──────────────────────────────────


class TestAutomaticTransitions:
    def test_archive_transition_creates_backup(self, skills_dir):
        _create_skill(skills_dir, "stale-skill")
        now = datetime(2026, 8, 1, tzinfo=UTC)
        old = (now - timedelta(days=100)).isoformat()
        _set_usage(skills_dir, "stale-skill", state="stale", managed_by_curator=True, last_activity_at=old, created_at=old)

        config = SimpleNamespace(stale_after_days=30, archive_after_days=90)
        apply_automatic_transitions(config, now=now)

        backups = list(get_backup_dir().glob("stale-skill-*.tar.gz"))
        assert len(backups) == 1
        assert not (skills_dir / "stale-skill").exists()


# ── Pinned skill protection ─────────────────────────────────────────────


class TestPinnedProtection:
    def test_pinned_skill_not_transitioned(self, skills_dir):
        _create_skill(skills_dir, "pinned-skill")
        now = datetime(2026, 8, 1, tzinfo=UTC)
        old = (now - timedelta(days=100)).isoformat()
        _set_usage(
            skills_dir, "pinned-skill",
            state="active", managed_by_curator=True, pinned=True,
            last_activity_at=old, created_at=old,
        )

        config = SimpleNamespace(stale_after_days=30, archive_after_days=90)
        result = apply_automatic_transitions(config, now=now)

        assert result["staled"] == []
        assert result["archived"] == []
        assert (skills_dir / "pinned-skill" / "SKILL.md").exists()
