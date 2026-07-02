# Testing

This repository is rebuilding its test suite around a small reliable core.

## Run The Core Tests

From the repository root:

```powershell
$env:PYTHONPATH='backend'
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

## What The Core Tests Cover

- Core modules import with the current package layout.
- Default config keeps risky background mutation disabled.
- `session_search` can index, dedupe, search, browse, and scroll around hits.
- `skill_manage` can create and patch custom skills while writing history and usage metadata.

## How To Add A Test

Prefer small tests that exercise one behavior.

Good test shape:

1. Build temporary state under `tmp_path`.
2. Inject a minimal config with `set_app_config`.
3. Mock LLM/security calls.
4. Assert a concrete file, return value, or record changed.

Avoid tests that require live model calls, network access, or the full app server.
