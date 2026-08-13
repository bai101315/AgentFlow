from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(LEETCODE_SESSION=)[^;\s]+", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|token|secret|cookie|session)\s*[:=]\s*['\"]?[^,'\"\s]{6,}"),
)


def stable_hash(value: Any) -> str:
    raw = _to_json_text(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def redact_text(text: str) -> tuple[str, bool]:
    redacted = text
    changed = False
    for pattern in _SECRET_PATTERNS:
        updated, count = pattern.subn("[REDACTED]", redacted)
        if count:
            changed = True
            redacted = updated
    return redacted, changed


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[: max(0, max_chars - 3)] + "...", True


def sanitize_preview(value: Any, *, max_chars: int) -> dict[str, Any]:
    raw_text = _to_json_text(value)
    redacted_text, redacted = redact_text(raw_text)
    preview, truncated = truncate_text(redacted_text, max_chars=max_chars)
    return {
        "preview": preview,
        "hash": stable_hash(value),
        "redacted": redacted,
        "truncated": truncated,
    }


def _to_json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(value)
