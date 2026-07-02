from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def _reset_app_config_after_test():
    try:
        from config.app_config import reset_app_config
    except Exception:
        yield
        return

    yield
    reset_app_config()


@pytest.fixture
def tmp_path():
    """Project-local temporary directory.

    The default pytest tmp_path uses the OS temp directory. On some Windows
    setups that directory is locked down, so the beginner test suite keeps
    temporary files inside the writable project tree.
    """
    root = BACKEND_ROOT / ".test-tmp"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
