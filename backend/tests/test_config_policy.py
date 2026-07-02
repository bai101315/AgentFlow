from __future__ import annotations

from config.app_config import AppConfig


def test_default_config_keeps_background_mutation_disabled():
    config = AppConfig.from_file()

    assert config.skill_evolution.enabled is True
    assert config.skill_evolution.auto_create is False
    assert config.background_review.enabled is False
    assert config.curator.enabled is False
    assert config.session_search.enabled is True
