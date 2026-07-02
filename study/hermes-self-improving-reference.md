# Hermes Agent Self-Improving 机制完全参考文档

## 概述

Hermes Agent 的 "self-improving"（自我改进）是其核心差异化特性。它不是单一机制，而是由**三层互补系统**构成的学习闭环：

```
┌─────────────────────────────────────────────────────────┐
│                   Self-Improving 三层架构                  │
├─────────────────────────────────────────────────────────┤
│  第1层：Background Review （实时学习，每个 Turn 后触发）    │
│  ├── Skill Auto-Creation （自动创建/更新技能）            │
│  └── Memory Auto-Save （自动保存记忆）                    │
│                                                         │
│  第2层：Curator （长期维护，每周触发）                      │
│  ├── 生命周期管理 （stale → archive）                     │
│  └── 技能合并 （umbrella-building consolidation）        │
│                                                         │
│  第3层：Session Search （跨会话召回）                      │
│  └── FTS5 全文搜索 + LLM 摘要                             │
└─────────────────────────────────────────────────────────┘
```

---

## 第1层：Background Review（背景审查 — 实时学习）

### 设计理念

每次用户 Turn 结束后，父 Agent **fork 出一个独立的子 AIAgent**（daemon 线程），回放本 Turn 的完整对话，判断是否有值得持久化的知识。子 Agent 运行完成后即销毁，**绝不触碰父 Agent 的对话上下文和 prompt cache**。

### 触发机制：Nudge 计数器

**Skill 触发路径：**

1. 每个 Tool-Calling 迭代（agent 调用一次 LLM + 工具）时，`agent._iters_since_skill += 1`
   - 源文件：`agent/conversation_loop.py` 第 669-671 行
2. 当 agent **主动调用 `skill_manage` 工具**时，计数器重置为 0（`agent/tool_executor.py` 第 321-322 行）——表示已经手动保存技能，不需要 nudge
3. Turn 结束时（`agent/turn_finalizer.py` 第 437-441 行），检查条件：
   ```python
   if (agent._skill_nudge_interval > 0
           and agent._iters_since_skill >= agent._skill_nudge_interval
           and "skill_manage" in agent.valid_tool_names):
       _should_review_skills = True
       agent._iters_since_skill = 0
   ```
4. 如果触发，调用 `agent._spawn_background_review(messages_snapshot, review_skills=True)`

**Memory 触发路径：**

与 skill 同理，但计数器是 `agent._turns_since_memory`（每次 Turn +1），阈值是 `memory.nudge_interval`（默认 10）。

**配置项：**

| 配置键 | 默认值 | 含义 |
|--------|--------|------|
| `skills.creation_nudge_interval` | 10 | 多少个工具调用迭代后触发 skill review |
| `memory.nudge_interval` | 10 | 多少个 Turn 后触发 memory review |

⚠️ **CLI 模式的 Pitfall**：每次 `hermes` 启动时 `_iters_since_skill` 重置为 0（`agent/agent_init.py` 第 1205 行）。如果 session 太短，Nudge 从未触发。解决：降低 `nudge_interval` 或使用 `hermes --continue` 累积 Turn。

### 执行流程

#### 步骤1：构建 Review Fork

**源文件：** `agent/background_review.py` 第 571-741 行（`_run_review_in_thread`）

```python
review_agent = AIAgent(
    model=parent_model,         # 继承父模型
    max_iterations=16,           # 最多 16 个工具调用
    quiet_mode=True,             # 静默模式
    skip_memory=True,            # 不连外部 memory provider
    parent_session_id=...,       # 标记父子关系
    enabled_toolsets=parent_toolsets,  # 工具集与父相同（保证 cache key 一致）
)
```

**关键隔离设计：**

| 属性 | 设置 | 原因 |
|------|------|------|
| `_cached_system_prompt` | 继承父的 | 命中同一个 Anthropic/OpenRouter prefix cache，节省 ~26% 成本 |
| `review_whitelist` | 仅 `memory` + `skill_manage` + `skill_view` + `skills_list` | 其他工具调用在运行时被拒绝 |
| `_skip_mcp_refresh = True` | 不做 MCP 刷新 | 防止新增 MCP 工具破坏 tools[] 字节一致性 |
| `compression_enabled = False` | 禁止压缩 | 避免压缩导致 session 分叉（#38727） |
| `_end_session_on_close = False` | 不结束 session | session 属于父 agent |
| `approval callback = auto-deny` | 自动拒绝危险命令 | 防止 background review 尝试交互式确认导致死锁（#15216） |

#### 步骤2：路由决策（Same Model vs Different Model）

**源文件：** `agent/background_review.py` 第 45-99 行（`_resolve_review_runtime`）

```python
# 默认：使用父模型 → 全量回放 → 利用预热缓存
parent = {"provider": ..., "model": ..., "routed": False}

# 如果配置了 auxiliary.background_review.{provider,model} 且与父不同
# → routed = True → 使用摘要回放 → 仅回放最近 24 条消息
```

当配置 `auxiliary.background_review.provider` 和 `auxiliary.background_review.model` 到不同的更便宜模型时，全量回放会浪费冷写入 token。此时 `_digest_history()` 将旧轮次压缩为摘要，只保留最近 24 条消息的完整内容。

#### 步骤3：运行 Review Agent

```python
review_agent.run_conversation(
    user_message=prompt + "\n\nYou can only call memory and skill management tools...",
    conversation_history=messages_snapshot,  # 或 digest（routed 时）
)
```

#### 步骤4：提取行动摘要

**源文件：** `agent/background_review.py` 第 362-541 行（`summarize_background_review_actions`）

Review Fork 完成后，函数扫描其内部的 tool 消息，提取所有成功的 `memory` 和 `skill_manage` 调用，构建用户可见的摘要行。过滤掉父对话中已存在的旧 tool 消息（避免重复显示 #14944）。

摘要示例：
```
💾 Self-improvement review: Memory ➕ "User prefers concise responses" · 📝 Skill 'hermes-agent' patched: "Use web_search first..." → "Prefer web_extract..."
```

通知模式控制（`background_review.notifications`）：

| 值 | 效果 |
|----|------|
| `off` | 不显示任何摘要 |
| `on` | 显示 "Memory updated" / "Skill created" 等简短消息 |
| `verbose` | 显示具体的 diff 预览（old → new） |

#### 步骤5：清理

- 关闭 review agent 的 memory provider
- 调用 `review_agent.close()`（但不结束父 session）
- 清除 approval callback

### Review Prompts（决策提示词）

**三个 Prompt，根据触发条件选择：**

| Prompt | 触发条件 | 文件位置 |
|--------|----------|----------|
| `_SKILL_REVIEW_PROMPT` | 仅 skill nudge 触发 | `agent/background_review.py` 第 170-273 行 |
| `_MEMORY_REVIEW_PROMPT` | 仅 memory nudge 触发 | 第 159-168 行 |
| `_COMBINED_REVIEW_PROMPT` | 两者同时触发 | 第 275-358 行 |

#### `_SKILL_REVIEW_PROMPT` 核心规则（关键信号检测）

**必须响应的信号（第一优先级）：**

1. **用户纠正你的风格/语气/格式/冗长度** — 这是 FIRST-CLASS skill signal。包括 "stop doing X"、"too verbose"、"don't format like this"、"why are you explaining"、"just give me the answer"、"you always do Y and I hate it"、"remember this"
2. **用户纠正你的工作流/方法/步骤顺序** — 编码为 skill 的 pitfall 或步骤
3. **出现了非平凡的技术/修复/调试模式** — 值得持久化
4. **加载过的 skill 被证明有误/缺少步骤/过时** — 立即 patch

**Action 优先级（从高到低）：**

1. **PATCH 当前加载的 Skill** — 如果本次会话通过 `/skill-name` 或 `skill_view` 加载了某个 skill
2. **PATCH 已有的 Umbrella Skill** — 通过 `skills_list` + `skill_view` 找到相关 Class-Level skill
3. **ADD SUPPORT FILE** — 在现有 umbrella 下添加 `references/`、`templates/`、`scripts/`
4. **CREATE NEW CLASS-LEVEL UMBRELLA** — 仅当完全找不到已有 skill 覆盖该类别

**明确禁止捕获的（Negative Filter）：**

- 环境依赖的失败：missing binaries、command not found、post-migration errors
- 对工具/功能的负面断言："browser tools do not work"、"X tool is broken"
- 会话中已解决的瞬态错误（retry 成功则学 retry pattern，不学原始错误）
- 一次性任务叙述："summarize today's market"、"analyze this PR"

**Naming 规则：**

- 必须是 CLASS-LEVEL 名称
- 不能是 PR 号、错误字符串、feature codename、library 名、"fix-X / debug-Y / audit-Z-today"

### Skill Provenance（技能来源追踪）

**源文件：** `tools/skill_provenance.py`

通过 `ContextVar` 区分技能的来源：

| 来源 | 标记 | 是否被 Curator 管理 |
|------|------|---------------------|
| Background Review Fork | `_memory_write_origin = "background_review"` | ✅ 是 |
| 前台 Agent（用户要求） | `"assistant_tool"` / `"foreground"` | ❌ 否 |

只有 `created_by == "agent"` 的技能才被 Curator 管理（`tools/skill_usage.py` 第 473-477 行）。

---

## 第2层：Curator（技能馆长 — 长期维护）

### 设计理念

Background Review 每次 Turn 后创建/更新技能，但从不清理或合并。Curator 定期检查整个技能库，执行生命周期管理和智能合并，防止技能库膨胀为数百个狭义的临时条目。

### 触发机制

**源文件：** `agent/curator.py` 第 1958-1976 行（`maybe_run_curator`）

```python
def maybe_run_curator(*, idle_for_seconds=None, on_summary=None):
    if not should_run_now():
        return None
    if idle_for_seconds is not None and idle_for_seconds < min_idle_s:
        return None  # Agent 正在活跃，不打断
    return run_curator_review(on_summary=on_summary)
```

**触发条件：**

1. `curator.enabled == True`（默认开启）
2. `curator.paused == False`
3. 距离上次运行超过 `curator.interval_hours`（默认 168 小时 = 7 天）
4. Agent 空闲时间超过 `curator.min_idle_hours`（默认 2 小时）—— 仅在 agent 空闲时触发
5. 首次安装后不会立即触发 — 先播种 `last_run_at`，等一个完整 interval 后再运行

**手动触发：**
```bash
hermes curator run              # 运行真实 pass
hermes curator run --dry-run    # 预览模式（只报告，不修改）
hermes curator run --consolidate # 强制开启合并（即使配置为 off）
```

### 执行流程

#### 阶段1：自动状态转换（无 LLM，纯函数）

**源文件：** `agent/curator.py` 第 291-369 行（`apply_automatic_transitions`）

对每个 Curator 管理的技能，基于最后活跃时间戳执行状态转换：

```
                        unused > 30 days
           active  ──────────────────────►  stale
              ▲                            │
              │     reused                  │  unused > 90 days
              └────────────────────────────┘
                                            │
                                            ▼
                                        archived
                                      (移至 .archive/)
```

**保护规则（永不自动转换）：**

- `pinned == True` 的技能
- Cron Job 引用的技能（即使 job 暂停/禁用）
- `PROTECTED_BUILTIN_SKILLS`（当前包括 `plan`）
- `use_count == 0` 且创建不足 30 天的技能（grace floor）

```python
# cron_referenced 技能被视为 "正在使用" 即使它们未达到 nudge 频率
cron_referenced = _cron_referenced_skills()
if name in cron_referenced:
    continue  # 跳过，不自动转换
```

#### 阶段2：LLM 合并审查（可选，off by default）

**源文件：** `agent/curator.py` 第 1480-1679 行（`run_curator_review`）

**默认关闭**（`curator.consolidate: false`）。开启后：

1. Fork 一个 AIAgent（类似 Background Review pattern）
2. 传入 `CURATOR_REVIEW_PROMPT` + 候选技能列表
3. Curator Agent 执行以下操作：

**合并策略（`CURATOR_REVIEW_PROMPT` 第 403-490 行）：**

| 操作 | 描述 |
|------|------|
| 前缀集群识别 | 扫描候选列表，按前缀（如 `hermes-config-*`、`gateway-*`、`ollama-*`）聚类 |
| Merge into existing | 集群中已有足够宽的 umbrella → patch 它，归档其他 |
| Create new umbrella | 没有现成 umbrella → `skill_manage action=create` 新 umbrella，归档旧条目 |
| Demote to support file | 某条目有窄但有用的内容 → 移到 umbrella 的 `references/`、`templates/`、`scripts/` |

**硬规则：**

- 永不删除，只有归档（`.archive/` 可恢复）
- 不碰 bundled / hub-installed / external-dir 技能
- 不碰 pinned 技能
- 不基于 `use_count` 跳过合并（计数器太新，证据不足）

### 状态持久化

**`.usage.json`：** `~/.hermes/skills/.usage.json`
- 每个技能的创建者、使用次数、查看次数、patch 次数、最后活跃时间、生命周期状态、pin 状态

**`.curator_state`：** `~/.hermes/skills/.curator_state`
- 上次运行时间、运行次数、上次摘要、暂停状态

**`.curator_suppressed`：** `~/.hermes/skills/.curator_suppressed`
- 被 curator 移除的 built-in skill 名单，防止 `hermes update` 重新播种

**`.bundled_manifest`：** `~/.hermes/skills/.bundled_manifest`
- 所有 bundled skill 的名单（`name:hash`），用于区分 bundled vs agent-created

**`.hub/lock.json`：** `~/.hermes/skills/.hub/lock.json`
- Hub-installed skill 的名单，Curator 永不触碰

---

## 第3层：Session Search（跨会话召回）

虽然不是 "self-improving" 的主流程，但它是学习闭环的一部分：让 Agent 能搜索过去的对话来获取上下文。

- FTS5 全文本搜索（SQLite）
- LLM 摘要化
- 在 Background Review 的 prompt 中不直接调用，但通过 `session_search` 工具在前台可用

---

## Memory（记忆系统 — Self-Improving 的姐妹系统）

Skill 捕获 "如何做某类任务"，Memory 捕获 "用户是谁"。

### 触发机制

与 Skill nudge 同理：`agent._turns_since_memory` 每个 Turn +1（在 `run_conversation` 开头），当 >= `memory.nudge_interval` 时在 turn_finalizer 触发 background review。

⚠️ **同样受到 CLI 重启重置的影响**（`_turns_since_memory` 重置为 0）。

### Memory Write Approval

**配置：** `memory.write_approval: true/false`

- `false`（默认）：Background Review 自动写入
- `true`：所有写入（包括 foreground 和 background）需要批准
  - CLI 前台：内联提示
  - Gateway/Background：暂存到 `/memory pending` → `/memory approve <id>` 审查

### 与 Skill 的分工

| 维度 | Memory | Skill |
|------|--------|-------|
| 存储内容 | 用户是谁、偏好、当前状态 | 如何做某类任务 |
| 用户偏好 | 记录偏好事实 | 将偏好嵌入工作流 |
| 示例 | "用户喜欢简洁回答" | "回答时不要多行解释"（嵌入对应 skill） |

`_SKILL_REVIEW_PROMPT` 明确要求：
> 用户偏好应同时写入 Memory 和对应 Skill。Memory 说 "用户喜欢什么"，Skill 说 "按这个要求做"。

---

## 完整数据流图

```
用户消息 ──► Conversation Loop (conversation_loop.py)
               │
               │ 每个 tool-calling 迭代：
               │   agent._iters_since_skill += 1
               │   (如果调用了 skill_manage，重置为 0)
               │
               ▼
         Turn Finalizer (turn_finalizer.py)
               │
               │ 检查 Nudge 条件：
               │   _iters_since_skill >= _skill_nudge_interval?
               │   _turns_since_memory >= _memory_nudge_interval?
               │
               ▼ (如果触发)
    AIAgent._spawn_background_review() (run_agent.py:1468)
               │
               │ threading.Thread(target=_run_review_in_thread, daemon=True)
               │
               ▼
    _run_review_in_thread() (background_review.py:571)
               │
               ├──► 1. _resolve_review_runtime() — 同模型→全量/cache || 不同→摘要
               ├──► 2. 创建隔离的 review_agent (AIAgent)
               ├──► 3. 设置 tool whitelist (仅 memory + skill)
               ├──► 4. 继承 cached_system_prompt (prefix cache 复用)
               ├──► 5. run_conversation(user_message=prompt, history=snapshot)
               ├──► 6. summarize_background_review_actions() → 用户摘要
               └──► 7. cleanup (close, clear whitelist)
               │
               ▼ (如果 memory/skill 工具被调用)
        memory() ──► MEMORY.md / USER.md / external provider
        skill_manage() ──► ~/.hermes/skills/<name>/SKILL.md
               │
               │ provenance: created_by = "agent"
               │
               ▼
        .usage.json 更新 (skill_usage.py)

--- 以上是第1层（实时），以下是第2层（周期） ---

        每 7 天（agent 空闲时）:
        maybe_run_curator() ──►
               │
               ├──► apply_automatic_transitions()
               │       │
               │       ├── active → stale (30 days unused)
               │       └── stale → archived (90 days unused)
               │
               └──► (如果 consolidate: true) run_curator_review()
                       │
                       ├── Fork AIAgent with CURATOR_REVIEW_PROMPT
                       └── 合并重叠技能 → umbrella → 归档窄条目
```

---

## 关键文件索引

| 文件 | 作用 |
|------|------|
| `agent/background_review.py` | Background Review 主逻辑：fork、prompts、摘要、路由 |
| `agent/turn_finalizer.py` | Turn 结束后的 nudge 检查 + 触发 spawn |
| `agent/conversation_loop.py` | `_iters_since_skill` 计数器递增 |
| `agent/tool_executor.py` | `skill_manage` / `memory` 调用时重置计数器 |
| `agent/agent_init.py` | 初始化 `_skill_nudge_interval` 和计数器默认值 |
| `run_agent.py` | `_spawn_background_review` + prompt 导入 |
| `agent/curator.py` | Curator 完整逻辑：状态转换、LLM 合并、调度 |
| `tools/skill_usage.py` | 技能使用遥测：`.usage.json`、生命周期状态、来源判断 |
| `tools/skill_provenance.py` | ContextVar 区分 foreground vs background review |
| `tools/skill_manager_tool.py` | `skill_manage` 工具实现 |
| `tools/memory_tool.py` | `memory` 工具实现 |
| `hermes_cli/config.py` | 默认配置值 |

---

## 配置速查

```yaml
# skills 配置
skills:
  creation_nudge_interval: 10    # 多少个工具迭代后触发 skill review（默认 10）

# memory 配置
memory:
  nudge_interval: 10             # 多少个 Turn 后触发 memory review（默认 10）
  memory_enabled: true           # 是否启用 MEMORY.md
  user_profile_enabled: true     # 是否启用 USER.md
  memory_char_limit: 2200        # MEMORY.md 字符上限
  user_char_limit: 1375          # USER.md 字符上限
  write_approval: false          # 是否需要批准 memory 写入

# curator 配置
curator:
  enabled: true                  # 是否启用 curator（默认 true）
  interval_hours: 168            # 运行间隔小时（默认 168 = 7天）
  min_idle_hours: 2              # agent 空闲多久后才触发（默认 2小时）
  stale_after_days: 30           # 多少天未使用标记为 stale
  archive_after_days: 90         # 多少天未使用标记为 archived
  consolidate: false             # LLM 合并 pass（默认关闭，避免 aux model cost）
  prune_builtins: true           # 是否允许 curator 处理 bundled built-in skills
  paused: false                  # 是否暂停 curator

# auxiliary model 路由（可选）
auxiliary:
  background_review:
    provider: openrouter         # 将 review fork 路由到更便宜的模型
    model: google/gemini-flash-1.5

# background review 通知
background_review:
  notifications: on              # off | on | verbose
```

---

## 常见问题与 Pitfalls

### 1. CLI 模式下 Skill/Memory 从不自动保存

**原因：** 每次 `hermes` 启动时 `_iters_since_skill` 和 `_turns_since_memory` 重置为 0。如果 session 少于 `nudge_interval` 个 Turn，Nudge 永远不触发。

**修复：**
```bash
hermes config set skills.creation_nudge_interval 3
hermes config set memory.nudge_interval 3
# 或使用更长的 session: hermes --continue
# 或显式要求：对 agent 说 "save that to memory"
```

### 2. Background Review 从来不创建技能

**检查清单：**
- Agent 是否调用了足够多的工具？（需要 `nudge_interval` 个工具迭代）
- Agent 是否已经手动调用了 `skill_manage`？（会重置计数器）
- 是否命中了 Negative Filter？（环境失败、一次任务、工具否定等不会被保存）

### 3. Curator 不运行

**检查：**
```bash
# 查看 curator 状态
python3 -c "
import json
from pathlib import Path
state = json.loads(Path.home().joinpath('.hermes/skills/.curator_state').read_text())
print('paused:', state.get('paused'))
print('last_run_at:', state.get('last_run_at'))
print('last_summary:', state.get('last_run_summary'))
"
```
**手动运行：** `hermes curator run --dry-run`

### 4. 技能被意外归档

- 归档不是删除 —— skill 被移到 `~/.hermes/skills/.archive/<name>/`
- 恢复：`hermes curator restore <name>`
- 如果一个 skill 很重要不想被归档：`hermes curator pin <name>`
