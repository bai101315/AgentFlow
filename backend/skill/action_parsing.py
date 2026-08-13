"""Tolerant parsing of model-proposed skill action payloads (JSON).

Both the background review middleware and the skill curator ask an LLM to
propose skill actions as strict JSON.  Models do not always comply: they wrap
the payload in ```json fences or surround it with prose.  These helpers parse
all of those shapes and fall back to an empty action list instead of raising,
so a bad model response degrades to a no-op review/consolidation run instead
of aborting the whole pass.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _coerce_actions(data: Any) -> list[dict[str, Any]]:
    """Normalize a parsed payload into a list of action dicts."""
    if isinstance(data, list):
        actions = data
    elif isinstance(data, dict):
        actions = data.get("actions")
    else:
        actions = None
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _extract_balanced_block(text: str, opener: str, closer: str) -> str | None:
    """Return the first top-level opener..closer block, ignoring strings."""
    start = text.find(opener)
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_string:
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_actions_json(raw: str) -> list[dict[str, Any]]:
    """Parse an actions payload from model output.

    Tolerates ```json code fences, bare objects/arrays, and surrounding
    prose.  Returns an empty list when no valid actions payload is found.
    """
    if not raw:
        return []
    text = raw.strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()

    try:
        return _coerce_actions(json.loads(text))
    except json.JSONDecodeError:
        pass

    found_empty = False
    for opener, closer in (("{", "}"), ("[", "]")):
        block = _extract_balanced_block(text, opener, closer)
        if block is None:
            continue
        try:
            actions = _coerce_actions(json.loads(block))
        except json.JSONDecodeError:
            continue
        if actions:
            return actions
        found_empty = True
    if found_empty:
        return []

    logger.warning("Could not parse actions JSON from model output; preview=%r", raw[:300])
    return []
