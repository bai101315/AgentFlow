"""Append-only trajectory samples (JSONL).

Writes are guarded by a process-local lock so concurrent traces in the same
process cannot interleave partial lines, and each line is written with a
single ``write`` call so the OS-level append is atomic even across processes.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_write_lock = threading.Lock()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    with _write_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
