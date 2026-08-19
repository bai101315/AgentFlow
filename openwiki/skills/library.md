---
type: Domain Guide
title: Skill Library
description: Public and custom skill discovery, parsing, enabled-state handling, progressive disclosure, support files, and change validation.
tags: [skills, discovery, prompts]
openwiki:
  roles: [domain, workflow]
  source_paths: [backend/skill/loader.py, backend/skill/parser.py, backend/tools/builtins/skill_tools.py, backend/agents/lead_agent/prompt.py]
  symbols: [load_skills, skills_list_tool, skill_view_tool]
  test_paths: [backend/tests/test_skill_view_tools.py, backend/tests/test_paths_config.py]
  validation_commands: [PYTHONPATH=backend python -m pytest backend/tests/test_skill_view_tools.py backend/tests/test_paths_config.py -q]
---

# Skill Library

Skills are reusable workflow documents discovered below configured `skills.path`. `load_skills` scans `public` and `custom`, skips hidden directories, parses every `SKILL.md`, lets later custom entries override the same name, applies enabled state from `ExtensionsConfig`, and sorts by name. Relative skill paths must resolve under the repository root; `test_paths_config.py` protects this because an incorrect parent calculation silently produces an empty library.

Progressive disclosure keeps prompts small. `skills_list` returns only name, description, category, and container location. `skill_view` returns one full `SKILL.md`, lists files under `references`, `templates`, `scripts`, and `assets`, and records a view signal. Reading a support file through normal file tools represents actual use. The lead prompt also maintains an enabled-skill cache; writes refresh that cache.

Public skills are read-only through the governed API. Custom skills are the writable/evolvable category. See [governed writes](governed-writes.md) for mutation policy and [curator lifecycle](curator-lifecycle.md) for activity state.

When adding parser/frontmatter fields, update `Skill` types, parser validation, list/view serialization, and prompt rendering. Test malformed frontmatter, nested discovery, hidden directories, public/custom name collisions, enabled filtering, support-file listing, and repository-root resolution. The narrow check is `PYTHONPATH=backend python -m pytest backend/tests/test_skill_view_tools.py backend/tests/test_paths_config.py -q`.
