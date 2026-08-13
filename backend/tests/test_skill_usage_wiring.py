from __future__ import annotations


def test_record_skill_use_splits_view_and_use(monkeypatch):
    """Reading SKILL.md is a view; reading a support file is a real use."""
    import sandbox.tools as sandbox_tools

    monkeypatch.setattr(sandbox_tools, "_get_skills_container_path", lambda: "/mnt/skills")
    monkeypatch.setattr(sandbox_tools, "_get_skills_host_path", lambda: None)

    recorded: list[tuple[str, str]] = []

    import skill.usage as usage

    monkeypatch.setattr(usage, "record_skill_access", lambda name, access: recorded.append((name, access)))

    sandbox_tools._record_skill_use("/mnt/skills/custom/my-skill/SKILL.md")
    sandbox_tools._record_skill_use("/mnt/skills/custom/my-skill/references/detail.md")
    sandbox_tools._record_skill_use("/mnt/skills/public/bootstrap/SKILL.md")

    assert recorded == [
        ("my-skill", "view"),
        ("my-skill", "use"),
        ("bootstrap", "view"),
    ]


def test_record_skill_use_skips_bookkeeping_and_foreign_paths(monkeypatch):
    import sandbox.tools as sandbox_tools

    monkeypatch.setattr(sandbox_tools, "_get_skills_container_path", lambda: "/mnt/skills")
    monkeypatch.setattr(sandbox_tools, "_get_skills_host_path", lambda: "/tmp/skills")

    recorded: list[tuple[str, str]] = []

    import skill.usage as usage

    monkeypatch.setattr(usage, "record_skill_access", lambda name, access: recorded.append((name, access)))

    # Hidden/archive/history paths must not count as usage.
    sandbox_tools._record_skill_use("/mnt/skills/custom/.archive/old-skill/SKILL.md")
    sandbox_tools._record_skill_use("/mnt/skills/custom/.history/demo-skill.jsonl")
    sandbox_tools._record_skill_use("/mnt/skills/custom/.usage.json")
    # Paths outside the skills roots are ignored.
    sandbox_tools._record_skill_use("/mnt/user-data/projects/skills/foo/SKILL.md")
    sandbox_tools._record_skill_use("")
    # Host-path form is recognized too.
    sandbox_tools._record_skill_use("/tmp/skills/custom/my-skill/SKILL.md")

    assert recorded == [("my-skill", "view")]
