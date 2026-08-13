# Self-Improving 完整验证手册

本文验证 AgentFlow 的完整链路：

```text
工具调用计数
  -> BackgroundReviewMiddleware 达到阈值
  -> 后台 ReviewScheduler
  -> 隔离 Review Agent
  -> security scanner
  -> skill_manage
  -> SKILL.md + history + usage + events
```

所有自动化 E2E 脚本都将产物写入 `/tmp/agentflow-*`，不会修改真实 `skills/` 和 `.agentflow/`。

## 1. 前置条件

从仓库根目录执行：

```bash
cd /home/pc/桌面/AgentFlow
```

确认配置：

```yaml
skill_evolution:
  enabled: true
  auto_create: true
  moderation_model_name: null
  require_background_provenance: true

background_review:
  enabled: true
  skill_nudge_interval: 2
  max_messages: 24
  review_model_name: null
  max_actions_per_review: 3
  timeout_seconds: 120
  max_concurrent_reviews: 1
```

`moderation_model_name: null` 和 `review_model_name: null` 表示复用前台有效默认模型。模型 API key 必须有效。

## 2. 一键验证顺序

### 2.1 无外部模型的单元和集成测试

```bash
uv run pytest \
  backend/tests/test_background_review_trigger.py \
  backend/tests/test_review_runtime.py \
  backend/tests/test_skill_security_scanner.py \
  backend/tests/test_skill_manage_core.py \
  backend/tests/test_skill_usage_wiring.py \
  backend/tests/test_skill_view_tools.py \
  -q
```

预期：全部通过。

它验证：

- 未达到阈值不 review；
- 达到阈值只提交一次；
- `skill_manage` 会重置计数；
- Review Agent 只有三个 Skill 工具；
- 父消息不会被修改；
- review timeout/failure 不影响前台；
- security scanner 使用前台有效默认模型；
- provenance、history、usage 正确；
- skill view/use 统计已接线。

### 2.2 权限与所有权验证

```bash
uv run python scripts/verify_review_permissions.py
```

预期最后显示：

```text
通过 11/11  全部通过
```

重点检查：

- 用户前台创建的技能：`created_by=foreground_user`；
- 后台不能 patch 用户技能；
- 后台创建的技能：`created_by=background_review`；
- 后台技能自动设置 `managed_by_curator=true`；
- 后台不能 delete；
- 前台可以删除自己的技能。

### 2.3 真实模型直连 Review 验证

```bash
uv run python scripts/verify_review_e2e.py
```

这个脚本绕过 middleware 触发计数，直接验证：

```text
Review Agent -> security scanner -> skill_manage -> 文件落盘
```

预期：

```text
status : completed
applied: 1
创建的技能目录: ['...']
created_by=background_review
managed_by_curator=True
review_started
skill_create
review_completed
端到端通过
```

如果得到 `status=completed, applied=0, summary=noop`，说明 Review 正常运行，但模型认为输入不足以形成持久技能。脚本内置的是强纠正样本，正常情况下应创建技能。

### 2.4 真实 middleware 自动触发验证

```bash
uv run python scripts/verify_review_auto_trigger.py
```

这是最完整的自动化验证，它不直接调用 `run_review()`，而是：

1. 构造两次新工具调用；
2. 调用 `BackgroundReviewMiddleware.after_agent()`；
3. 由 middleware 达到阈值并提交后台任务；
4. 等待 ReviewScheduler 完成；
5. 验证技能、history、usage 和 event lineage。

预期最后显示：

```text
后台线程完成: True
新建技能: ['...']
事件序列: ['review_started', 'skill_create', 'review_completed']
created_by=background_review managed_by_curator=True
PASS: middleware 自动触发 self-improving 并创建技能
```

### 2.5 完整 backend 回归

```bash
uv run pytest backend/tests -q
```

当前已知基线：

```text
76 passed
```

注意：当前 `make test` 错误地指向不存在的 `tests/`，因此本项目应使用上述实际命令。`make lint` 会扫描整个历史仓库并命中大量既有问题；本功能的定向 lint 命令见下一节。

### 2.6 定向 lint 与编译检查

```bash
uv run ruff check \
  backend/skill/security_scanner.py \
  backend/agents/middlewares/background_review_middleware.py \
  backend/agents/review_agent/runtime.py \
  backend/tools/skill_manage_tool.py \
  backend/tests/test_skill_security_scanner.py \
  scripts/verify_review_e2e.py \
  scripts/verify_review_auto_trigger.py \
  scripts/verify_review_permissions.py

uv run python -m py_compile \
  backend/skill/security_scanner.py \
  backend/agents/middlewares/background_review_middleware.py \
  backend/agents/review_agent/runtime.py \
  backend/tools/skill_manage_tool.py \
  scripts/verify_review_e2e.py \
  scripts/verify_review_auto_trigger.py \
  scripts/verify_review_permissions.py
```

## 3. CLI 人工黑盒验证

自动化验证通过后，再验证真实 `main.py` 用户路径。

### 3.1 启动

```bash
uv run python main.py --new-session
```

### 3.2 使用强纠正对话

第一轮发送一个必然调用工具的问题，例如：

```text
请读取 backend/skill/usage.py，解释 read_usage 的定义和调用方，必须使用工具核实。
```

然后明确纠正流程：

```text
不对。这个仓库有 codegraph 索引。以后遇到解释函数、查符号定义和调用链的任务，必须先运行 codegraph explore <symbol>，只有 codegraph 没结果时才退回 read_file。请现在按这个方法重做。
```

最后强化：

```text
对，就是这样。以后这一类任务都按这个流程。
```

要点：`skill_nudge_interval: 2` 统计的是新工具调用，不是用户消息数。必须实际产生至少两个 tool call。

退出前等待后台任务，或直接输入：

```text
exit
```

`main.py` 会有界等待后台 review。

### 3.3 检查真实产物

只在你明确要检查真实项目数据时执行：

```bash
find skills/custom -mindepth 1 -maxdepth 2 -name SKILL.md -print
```

新技能应位于：

```text
skills/custom/<skill-name>/SKILL.md
```

检查 usage：

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path('skills/custom/.usage.json')
data = json.loads(p.read_text(encoding='utf-8'))
for name, rec in data.get('skills', {}).items():
    print(name, rec.get('created_by'), rec.get('managed_by_curator'), rec.get('state'))
PY
```

后台创建的技能必须满足：

```text
created_by == background_review
managed_by_curator == True
state == active
```

检查 history：

```bash
python - <<'PY'
import json
from pathlib import Path
for p in Path('skills/custom/.history').glob('*.jsonl'):
    rows = [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
    if rows:
        row = rows[-1]
        print(p.name, row.get('action'), row.get('origin'), row.get('thread_id'), row.get('parent_thread_id'))
PY
```

检查事件：

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path('.agentflow/self_improvement/events.jsonl')
if not p.exists():
    print('NO EVENTS FILE')
else:
    for line in p.read_text(encoding='utf-8').splitlines()[-20:]:
        event = json.loads(line)
        print(event.get('ts'), event.get('event'), event.get('parent_thread_id'), event.get('applied'), event.get('reason'))
PY
```

成功事件序列：

```text
review_started
skill_create 或 skill_patch
review_completed (applied >= 1)
```

## 4. 如何判断到底卡在哪一层

| 观察结果 | 说明 | 检查位置 |
|---|---|---|
| 没有 `review_started` | 未达到触发条件或 middleware 未装配 | config、tool call 数、`test_background_review_trigger.py` |
| `review_started` 后 `review_failed` | Review 模型、超时或运行时失败 | `debug.log`、event `reason` |
| `review_completed applied=0 summary=noop` | 机制正常，模型判断没有稳定经验 | 使用强纠正 + 成功重做 + 用户强化样本 |
| 日志出现 security scan 401 | moderation model/凭据错误 | `skill_evolution.moderation_model_name`、默认模型解析 |
| Review 调用了 skill_manage 但报 permission | 目标是用户拥有的技能 | 应创建新 background skill 或只修改 curator-managed 技能 |
| 有 SKILL.md，无 usage/history | 写入后元数据链失败 | `skill_manage_tool.py`、`.history/`、`.usage.json` |
| 有 skill_create，无 review_completed | Review 后续超时或退出过早 | `timeout_seconds`、退出 drain |
| CLI 很快退出且没有产物 | daemon review 未完成 | 使用正常 `exit`，让 `_drain_background_reviews()` 执行 |

## 5. 日志检索

```bash
rg -n "Background review|security scan|skill_create|review_failed|Created custom skill" debug.log
```

如果没有 `rg`：

```bash
grep -nE "Background review|security scan|skill_create|review_failed|Created custom skill" debug.log
```

关键日志包括：

```text
Background review <id> using model <name>
Background review tool '<name>' failed: ...
Background review <id> timed out ...
Skill security scan model call failed ...
```

## 6. 验收标准

只有以下条件全部满足，才算功能验证完成：

- trigger 单测证明达到阈值只提交一次；
- Review Agent 工具面只有 `skills_list`、`skill_view`、`skill_manage`；
- 权限脚本 11/11；
- 真实 Review E2E 创建临时技能；
- 真实 middleware 自动触发 E2E 创建临时技能；
- Skill frontmatter 合法；
- usage 显示 background provenance；
- history 包含 parent thread lineage；
- events 包含 `review_started -> skill_create/patch -> review_completed`；
- 完整 `backend/tests` 回归通过；
- 本功能相关文件 Ruff 和 py_compile 通过。

## 7. llm-wiki 视角下的验证原则

Self-improving 和 llm-wiki 都不是“模型生成了一段文本就算成功”，而是知识编译流水线。必须同时验证：

1. Trigger：何时值得编译；
2. Schema：SKILL.md 结构是否合法；
3. Provenance：知识来自哪个 thread 和 origin；
4. Index/usage：后续 Agent 是否能发现并使用它；
5. Log/history：变更是否可追踪；
6. Governance：后台只能改自己拥有的知识，且自动动作可恢复。

因此只看到模型返回“我学会了”不算通过；必须看到真实 `SKILL.md`、usage、history 和 events。
