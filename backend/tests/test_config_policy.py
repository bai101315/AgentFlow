from __future__ import annotations

import pytest
from config.app_config import AppConfig
from config.self_improvement_config import CuratorConfig
from pydantic import ValidationError


def test_default_config_matches_self_improvement_contract():
    """config.yaml must match the migration contract in docs/SELF_IMPROVING_MIGRATION.md §11.

    Background review may be enabled, but the curator stays off until its
    lifecycle, backup and restore paths are implemented and tested.
    """
    config = AppConfig.from_file()

    assert config.skill_evolution.enabled is True
    assert config.skill_evolution.auto_create is True
    assert config.skill_evolution.require_background_provenance is True
    assert config.background_review.enabled is True
    assert config.background_review.max_concurrent_reviews == 1
    assert config.curator.enabled is True
    assert config.curator.consolidate is True
    assert config.session_search.enabled is True


def test_default_config_curator_schedule_is_not_always_due():
    """§9.3: the always-due / same-day-archive combination must not ship."""
    curator = AppConfig.from_file().curator

    assert curator.interval_hours >= 1
    assert curator.archive_after_days > curator.stale_after_days


def test_curator_rejects_always_due_interval():
    with pytest.raises(ValidationError):
        CuratorConfig(interval_hours=0)


def test_curator_rejects_archive_window_not_after_stale_window():
    with pytest.raises(ValidationError):
        CuratorConfig(stale_after_days=30, archive_after_days=30)
