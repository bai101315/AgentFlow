"""手动验证：后台 review 只能改自己创建的技能，不能碰用户手动创建的。

在临时目录里操作，不会动你真实的 skills/ 。
用法：  uv run python scripts/verify_review_permissions.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))
sys.path.insert(0, str(_REPO_ROOT))

import tools.skill_manage_tool as sm  # noqa: E402
from config.app_config import set_app_config  # noqa: E402
from skill.security_scanner import ScanResult  # noqa: E402
from tools.skill_manage_tool import _skill_manage_impl  # noqa: E402

OK = "\033[32mOK\033[0m"
BAD = "\033[31mFAIL\033[0m"


def skill_md(name):
    return f"---\nname: {name}\ndescription: Demo workflow\n---\n\n# {name}\n"


def runtime(thread_id="manual-thread"):
    return SimpleNamespace(context={"thread_id": thread_id}, config={"configurable": {"thread_id": thread_id}})


async def scan(*a, **k):
    return ScanResult(decision="allow", reason="ok")


async def refresh():
    return None


def check(label, passed):
    print(f"  [{OK if passed else BAD}] {label}")
    return passed


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="agentflow-verify-"))
    skills_root = tmp / "skills"
    set_app_config(
        SimpleNamespace(
            skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills"),
            skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None, require_background_provenance=True),
        )
    )
    sm.scan_skill_content = scan
    sm.refresh_skills_system_prompt_cache_async = refresh

    results = []
    print(f"\n临时 skills 目录: {skills_root}\n")

    # 1. 用户在前台手动创建一个技能
    print("1) 用户手动创建 user-owned-skill（前台）")
    await _skill_manage_impl(runtime=runtime(), action="create", name="user-owned-skill", content=skill_md("user-owned-skill"))
    usage = json.loads((skills_root / "custom" / ".usage.json").read_text())["skills"]["user-owned-skill"]
    results.append(check(f"created_by == foreground_user (实际: {usage['created_by']})", usage["created_by"] == "foreground_user"))
    results.append(check(f"managed_by_curator is False (实际: {usage['managed_by_curator']})", usage["managed_by_curator"] is False))

    # 2. 后台 review 试图改它 —— 必须被拒
    print("\n2) 后台 review 试图 patch 用户的技能")
    try:
        await _skill_manage_impl(
            runtime=runtime(), action="patch", name="user-owned-skill",
            find="Demo workflow", replace="被后台偷偷改了",
            origin="background_review", execution_context="background_review",
        )
        results.append(check("应该抛 PermissionError", False))
    except PermissionError as exc:
        results.append(check(f"被拒绝: {exc}", True))
    body = (skills_root / "custom" / "user-owned-skill" / "SKILL.md").read_text()
    results.append(check("文件内容没被改动", "被后台偷偷改了" not in body))

    # 3. 后台 review 创建自己的技能 —— 允许，且自动纳管
    print("\n3) 后台 review 创建 agent-learned-skill")
    await _skill_manage_impl(
        runtime=runtime(), action="create", name="agent-learned-skill", content=skill_md("agent-learned-skill"),
        origin="background_review", execution_context="background_review",
    )
    usage = json.loads((skills_root / "custom" / ".usage.json").read_text())["skills"]["agent-learned-skill"]
    results.append(check(f"created_by == background_review (实际: {usage['created_by']})", usage["created_by"] == "background_review"))
    results.append(check(f"managed_by_curator is True (实际: {usage['managed_by_curator']})", usage["managed_by_curator"] is True))

    # 4. 后台 review 改自己的技能 —— 允许
    print("\n4) 后台 review patch 自己创建的技能")
    await _skill_manage_impl(
        runtime=runtime(), action="patch", name="agent-learned-skill",
        find="Demo workflow", replace="Improved workflow",
        origin="background_review", execution_context="background_review",
    )
    body = (skills_root / "custom" / "agent-learned-skill" / "SKILL.md").read_text()
    results.append(check("patch 生效", "Improved workflow" in body))
    usage = json.loads((skills_root / "custom" / ".usage.json").read_text())["skills"]["agent-learned-skill"]
    results.append(check(f"created_by 未被 patch 覆盖 (实际: {usage['created_by']})", usage["created_by"] == "background_review"))

    # 5. 后台 review 不能删除
    print("\n5) 后台 review 试图删除自己的技能")
    try:
        await _skill_manage_impl(
            runtime=runtime(), action="delete", name="agent-learned-skill",
            origin="background_review", execution_context="background_review",
        )
        results.append(check("应该抛 PermissionError", False))
    except PermissionError as exc:
        results.append(check(f"被拒绝: {exc}", True))
    results.append(check("技能仍然存在", (skills_root / "custom" / "agent-learned-skill" / "SKILL.md").exists()))

    # 6. 用户可以删除自己的技能（前台不受限）
    print("\n6) 用户在前台删除自己的技能")
    await _skill_manage_impl(runtime=runtime(), action="delete", name="user-owned-skill")
    results.append(check("删除成功", not (skills_root / "custom" / "user-owned-skill").exists()))

    print(f"\n{'=' * 50}")
    print(f"通过 {sum(results)}/{len(results)}" + ("  全部通过 ✅" if all(results) else "  有失败 ❌"))
    print(f"可以查看历史记录: {skills_root / 'custom' / '.history'}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
