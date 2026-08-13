"""真实验证 BackgroundReviewMiddleware 自动达到阈值并创建技能。

使用真实 review/moderation 模型，但所有技能、history、usage 和 events
写入临时目录，不修改项目的真实 skills/ 或 .agentflow/。

用法：uv run python scripts/verify_review_auto_trigger.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))
sys.path.insert(0, str(_REPO_ROOT))

from agents.middlewares.background_review_middleware import BackgroundReviewMiddleware  # noqa: E402
from agents.review_agent.runtime import get_review_scheduler, resolve_review_model_name  # noqa: E402
from config import get_app_config  # noqa: E402


def _message(*, msg_type: str, content: str = "", tool_calls=None, name: str = ""):
    return SimpleNamespace(
        type=msg_type,
        content=content,
        tool_calls=tool_calls,
        additional_kwargs={},
        name=name,
        tool_call_id="",
    )


def _created_skills(skills_root: Path) -> list[str]:
    custom = skills_root / "custom"
    if not custom.exists():
        return []
    return sorted(
        path.name
        for path in custom.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="agentflow-auto-trigger-"))
    skills_root = tmp / "skills"
    (skills_root / "custom").mkdir(parents=True)
    (skills_root / "public").mkdir(parents=True)
    home = tmp / "home"
    home.mkdir()

    config = get_app_config()
    config.skills.path = str(skills_root)
    config.skill_evolution.enabled = True
    config.background_review.enabled = True
    config.background_review.skill_nudge_interval = 2
    config.background_review.max_actions_per_review = 3
    config.background_review.timeout_seconds = 180
    config.background_review.max_concurrent_reviews = 1

    import config.paths as paths_module

    paths_module._paths = paths_module.Paths(base_dir=home)

    thread_id = "auto-trigger-parent"
    runtime = SimpleNamespace(context={"thread_id": thread_id})
    messages = [
        _message(msg_type="human", content="帮我解释 read_usage 是做什么的。"),
        _message(
            msg_type="ai",
            content="我先逐个读文件。",
            tool_calls=[
                {"id": "auto-call-1", "name": "read_file", "args": {"path": "backend/skill/usage.py"}},
            ],
        ),
        _message(msg_type="tool", name="read_file", content="def read_usage(): ..."),
        _message(msg_type="ai", content="它读取 usage 文件。"),
        _message(
            msg_type="human",
            content=(
                "不对。这个仓库有 codegraph 索引。以后遇到解释函数、查符号调用链的任务，"
                "必须先运行 codegraph explore <symbol>，只有索引没有结果时才退回 read_file。"
            ),
        ),
        _message(
            msg_type="ai",
            content="明白，我改用 codegraph 验证。",
            tool_calls=[
                {"id": "auto-call-2", "name": "bash", "args": {"command": "codegraph explore read_usage"}},
            ],
        ),
        _message(
            msg_type="tool",
            name="bash",
            content="read_usage callers: update_skill_usage_for_write, record_skill_access, is_curator_managed",
        ),
        _message(msg_type="ai", content="codegraph 一次返回了定义和调用链。"),
        _message(msg_type="human", content="对，就是这样，以后都按这个流程。"),
        _message(msg_type="ai", content="收到。"),
    ]

    print(f"\n临时根目录: {tmp}")
    print(f"review 模型: {resolve_review_model_name(config.background_review.review_model_name)}")
    print("触发条件: 2 个新工具调用")

    middleware = BackgroundReviewMiddleware()
    before = _created_skills(skills_root)
    result = middleware.after_agent({"messages": messages}, runtime)
    state_after_submit = middleware._states[thread_id]

    print(f"after_agent 返回: {result!r}")
    print(f"提交后 review_running: {state_after_submit.review_running}")
    print(f"提交后计数器: {state_after_submit.tool_calls_since_skill}")

    scheduler = get_review_scheduler()
    idle = scheduler.wait_for_idle(timeout=210)
    after = _created_skills(skills_root)
    new_skills = sorted(set(after) - set(before))

    usage_path = skills_root / "custom" / ".usage.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {"skills": {}}
    events_path = home / "self_improvement" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()] if events_path.exists() else []
    event_names = [event.get("event") for event in events]

    print(f"后台线程完成: {idle}")
    print(f"新建技能: {new_skills or '无'}")
    print(f"事件序列: {event_names or '无'}")

    provenance_ok = bool(new_skills)
    for name in new_skills:
        record = usage.get("skills", {}).get(name, {})
        history_path = skills_root / "custom" / ".history" / f"{name}.jsonl"
        history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
        skill_path = skills_root / "custom" / name / "SKILL.md"
        print(f"\n{name}/SKILL.md:\n{skill_path.read_text(encoding='utf-8')[:1000]}")
        print(
            "provenance: "
            f"created_by={record.get('created_by')} "
            f"managed_by_curator={record.get('managed_by_curator')}"
        )
        provenance_ok = provenance_ok and record.get("created_by") == "background_review"
        provenance_ok = provenance_ok and record.get("managed_by_curator") is True
        provenance_ok = provenance_ok and bool(history)
        provenance_ok = provenance_ok and history[0].get("parent_thread_id") == thread_id

    passed = all(
        [
            result is None,
            idle,
            bool(new_skills),
            provenance_ok,
            "review_started" in event_names,
            "skill_create" in event_names,
            "review_completed" in event_names,
        ]
    )

    print("\n" + "=" * 64)
    print("PASS: middleware 自动触发 self-improving 并创建技能" if passed else "FAIL: 自动触发链路未完整通过")
    print(f"证据保留在: {tmp}")
    print(f"清理命令: rm -rf {tmp}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
