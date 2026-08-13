"""手动验证：后台 review 端到端真跑模型，确认能创建技能。

会真实调用模型。技能写到临时目录，不碰你真实的 skills/ 。
用法：  uv run python scripts/verify_review_e2e.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Both roots are needed: ``backend`` for the app's own flat imports, and the
# repo root so config entries like ``backend.models.patched_deepseek`` resolve.
sys.path.insert(0, str(_REPO_ROOT / "backend"))
sys.path.insert(0, str(_REPO_ROOT))

from agents.review_agent.runtime import ReviewRequest, resolve_review_model_name, run_review  # noqa: E402
from config import get_app_config  # noqa: E402

# 一段带强纠正信号的对话：用户纠正了工作方法，且纠正后的做法有效。
# 按 §6.3 这属于应该沉淀成 skill 的第一类信号。
TRANSCRIPT = """user: 帮我看看 backend/skill/usage.py 里的 read_usage 函数是干什么的

assistant: 我来读一下这个文件。
[tool_calls: read_file]

tool:read_file: def read_usage() -> dict[str, Any]: ...（省略）

assistant: read_usage 读取 .usage.json，解析失败时返回空结构。

user: 不对，你这样查代码效率太低了。这个仓库有 codegraph 索引，以后遇到「这个函数是干什么的」「这段逻辑在哪」这类查代码的任务，
你应该先跑 codegraph explore "<符号名>"，它会一次返回相关符号的源码和调用链，包括 grep 追不到的动态分发。
只有 codegraph 里没有的时候才退回 read_file 和 grep。

assistant: 明白，我重新用 codegraph 查。
[tool_calls: bash]

tool:bash: $ codegraph explore "read_usage"
  read_usage (backend/skill/usage.py:66)
  callers: update_skill_usage_for_write, record_skill_access, is_curator_managed
  ...

assistant: 用 codegraph 一次就拿到了完整调用链：read_usage 有三个调用方，
其中 is_curator_managed 决定技能是否纳入 curator 自动治理。这比逐个 read_file 快得多。

user: 对，就是这样。以后都这么干。
"""


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="agentflow-e2e-"))
    skills_root = tmp / "skills"
    (skills_root / "custom").mkdir(parents=True)
    (skills_root / "public").mkdir(parents=True)

    # 把技能根指向临时目录；其余配置（模型、凭据）全用真实的。
    config = get_app_config()
    config.skills.path = str(skills_root)
    home = tmp / "home"
    home.mkdir()
    import config.paths as paths_module

    paths_module._paths = paths_module.Paths(base_dir=home)

    print(f"\n临时 skills 目录: {skills_root}")
    print(f"review 使用模型: {resolve_review_model_name(None)}")
    print("\n正在跑 review（真实调用模型，可能要 20-60 秒）...\n")

    request = ReviewRequest(
        transcript=TRANSCRIPT,
        parent_thread_id="e2e-parent-thread",
        max_actions=3,
        timeout_seconds=180,
    )
    result = await run_review(request)

    print(f"status : {result.status}")
    print(f"applied: {len(result.applied)}")
    for line in result.applied:
        print(f"   - {line}")
    print(f"summary: {result.summary[:300]}")
    if result.error:
        print(f"error  : {result.error}")

    created = sorted(
        p.name
        for p in (skills_root / "custom").iterdir()
        if p.is_dir() and (p / "SKILL.md").is_file()
    )
    print(f"\n创建的技能目录: {created or '（无）'}")

    for name in created:
        skill_md = skills_root / "custom" / name / "SKILL.md"
        print(f"\n{'─' * 60}\n{name}/SKILL.md\n{'─' * 60}")
        print(skill_md.read_text(encoding="utf-8")[:1200])

    usage_file = skills_root / "custom" / ".usage.json"
    if usage_file.exists():
        print(f"\n{'─' * 60}\n.usage.json（provenance）\n{'─' * 60}")
        for name, rec in json.loads(usage_file.read_text())["skills"].items():
            print(f"  {name}: created_by={rec['created_by']}  managed_by_curator={rec['managed_by_curator']}")

    events_file = home / "self_improvement" / "events.jsonl"
    if events_file.exists():
        print(f"\n{'─' * 60}\nevents.jsonl\n{'─' * 60}")
        for line in events_file.read_text().splitlines():
            e = json.loads(line)
            print(f"  {e['event']}: applied={e.get('applied')} parent={e.get('parent_thread_id')}")

    ok = result.status == "completed" and len(created) > 0
    print(f"\n{'=' * 60}")
    print("✅ 端到端通过：后台 review 成功创建了技能" if ok else f"❌ 未创建技能（status={result.status}）")
    print(f"\n临时目录保留供你检查: {tmp}")
    print(f"清理: rm -rf {tmp}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
