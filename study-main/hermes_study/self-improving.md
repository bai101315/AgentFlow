# 自我进化
整个机制分四层：触发 → 审查 → 创建 → 维护。

## 触发 —— 什么时候做审查

每次对话轮次结束时，conversation_loop.py 检查两个计数器：

```python
conversation_loop.py 第 655-657 行
if agent._skill_nudge_interval > 0 and "skill_manage" in agent.valid_tool_names:
    agent._iters_since_skill += 1          # 每轮工具调用 +1


然后在返回结果前（第 4046-4051 行）：

python
_should_review_skills = False
if (agent._skill_nudge_interval > 0
        and agent._iters_since_skill >= agent._skill_nudge_interval
        and "skill_manage" in agent.valid_tool_names):
    _should_review_skills = True
    agent._iters_since_skill = 0           # 重置计数器


如果 _should_review_skills 为 True，且对话正常结束（没被中断），第 4062 行：

python
if final_response and not interrupted and (_should_review_memory or _should_review_skills):
    agent._spawn_background_review(
        messages_snapshot=list(messages),
        review_memory=_should_review_memory,
        review_skills=_should_review_skills,
    )


你当前的配置：
yaml
skills:
    creation_nudge_interval: 15   # 每 15 轮工具调用触发一次审查
```

## 审查 —— 一个"克隆 Agent"悄悄评估
这是整个机制的核心。_spawn_background_review 不是调一个函数，而是 fork 出一个完整的子 AIAgent，在 daemon 线程中运行。关键代码在 background_review.py 第 393-471 行

```python
review_agent = AIAgent(
    model=agent.model,           # 继承父 agent 的 DeepSeek V4
    provider=agent.provider,
    base_url=parent_runtime["base_url"],
    api_key=parent_runtime["api_key"],
    max_iterations=16,           # 独立上限，不会无限循环
    quiet_mode=True,             # 静默模式，不打印日志
    skip_memory=True,            # 不污染外部记忆
    parent_session_id=agent.session_id,
)
```

★ 继承父 agent 的系统提示词，共享 prefix cache
review_agent._cached_system_prompt = agent._cached_system_prompt

★ 工具白名单：只能用 memory + skill management
review_whitelist = {"memory", "skill_manage", "skill_view", "skills_list"}
set_thread_tool_whitelist(review_whitelist)

然后给这个子 agent 发送一段精心设计的审查 prompt：

```python
review_agent.run_conversation(
    user_message=_SKILL_REVIEW_PROMPT,       # ← 那个 100 行的审查指令
    conversation_history=messages_snapshot,   # ← 完整对话历史
)
```

审查 prompt 的核心判断逻辑（_SKILL_REVIEW_PROMPT 全文 100 行）：

"Be ACTIVE — most sessions produce at least one skill update."

判断信号（任一触发即需行动）：
    ✓ 用户纠正了你的风格/格式/语气/啰嗦程度
    "stop doing X", "don't format like this", "I hate when you Y"
    → 第一优先级！更新相关 skill

    ✓ 用户纠正了你的工作流/方法/步骤
    → 把纠正编码为 skill 中的 pitfall 或显式步骤

    ✓ 出现了有价值的技巧/修复方法/调试路径/工具使用模式
    → 捕获到 skill

    ✓ 本 session 中加载的某个 skill 被发现是错误的/缺少步骤/过时的
    → 马上 patch

行动优先级：
    1. PATCH 当前加载的 skill（最相关）
    2. PATCH 已有的 umbrella skill
    3. 添加 support file 到已有 skill
    4. 创建全新的 skill（仅在无已有 skill 覆盖时）

不应捕获的：
    ✗ 环境依赖的一次性错误（缺少二进制、未配置凭证）
    ✗ "XX工具坏了"这种负面断言（修好后还会被引用几个月）
    ✗ 已解决的临时错误
    ✗ 一次性任务叙述

"Nothing to save." 是合法的，但不应该是默认选择。

## 创建 —— skill_manage 工具

审查 agent 判定"需要创建 skill"后，调用 skill_manage 工具。这个工具有 6 种 action：

```
skill_manage(
    action="create",      # 创建新 SKILL.md
    name="debug-python",  # 类级别名称
    content="---\nname: debug-python\n---\n\n# Python Debugging\n\n...",
    category="software-development"  # 可选分类
)

skill_manage(
    action="patch",       # 精确替换（推荐用于修复）
    name="hermes-agent",
    old_string="...",
    new_string="..."
)

skill_manage(
    action="edit",        # 完全重写
    name="...",
    content="新的完整 SKILL.md"
)

skill_manage(
    action="write_file",  # 添加 support file
    name="hermes-agent",
    file_path="references/prompt-cache.md",
    file_content="..."
)

skill_manage(action="delete", name="...")      # 删除
skill_manage(action="remove_file", ...)        # 移除文件


创建的文件结构：

~/.hermes/skills/
    software-development/
    debug-python/
        SKILL.md             ← YAML frontmatter + Markdown body
        references/
        common-pitfalls.md ← 会话特定细节或知识库
        templates/
        debug-script.py    ← 可复用的模板
        scripts/
        verify-pytest.sh   ← 可运行的脚本
```

## 维护 —— Curator 定期整理

除了实时审查，还有一个 Curator 守护进程 负责长期维护。agent/curator.py 的前 60 行：

```python
Curator — background skill maintenance orchestrator.

Runs inactivity-triggered:
    - Auto-transition lifecycle states (stale → archive)
    - Spawn a review agent that can pin / archive / consolidate / patch
    - Persist state in .curator_state

Strict invariants:
    - Only touches agent-created skills
    - Never auto-deletes — only archives
    - Pinned skills bypass all auto-transitions

配置：
yaml
curator:
    enabled: true
    interval_hours: 168        # 每 7 天运行一次
    min_idle_hours: 2          # 必须空闲 2 小时以上
    stale_after_days: 30       # 30 天未使用 → 标记为 stale
    archive_after_days: 90     # 90 天未使用 → 归档
```


# 技能的使用流程


1. 系统提示词注入——每次构建系统提示词时，prompt_builder.py 会扫描 ~/.hermes/skills/ 目录，把所有 SKILL.md 的 frontmatter 提取出来，生成 skill 索引。你当前 session 的系统提示词里就有这样一段（就是你实际看到的那个）：


Skills (mandatory)
Before replying, scan the skills below. If a skill matches...
<available_skills>
autonomous-ai-agents: Skills for spawning and orchestrating...
creative: Creative content generation...
research: Skills for academic research...
...
</available_skills>

2. Skill 匹配加载——当你的问题和某个 skill 的描述匹配时，agent 被指示先 skill_view(name) 加载它的完整内容，然后按里面的步骤执行。她的技能提示词说：

If a skill matches or is even partially relevant to your task, 
you MUST load it with skill_view(name) and follow its instructions.
Err on the side of loading.

3. 动态更新——如果 agent 加载了一个 skill 后发现它有误，会立刻 skill_manage(action="patch") 修复它。


    
    
对话进行中
    │
    ├── _iters_since_skill 累积（每轮工具调用 +1）
    │
    ├── 达到 nudge_interval (15)? 
    │   ├── 否 → 跳过
    │   └── 是 → _should_review_skills = True
    │
    └── 对话结束，未被中断
        │
        └── spawn_background_review()
            │
            ├── Fork 新 AIAgent (同 model, 同 credentials, 同 prompt cache)
            │   工具白名单: [memory, skill_manage, skill_view, skills_list]
            │
            ├── 发送 _SKILL_REVIEW_PROMPT + 完整对话历史
            │
            ├── 子 agent 分析对话：
            │   ├── 用户有没有纠正我？
            │   ├── 我有没有发现新技巧？
            │   ├── 我加载的 skill 有没有错误？
            │   └── 有没有类级别的可复用模式？
            │
            ├── 决策 + 执行：
            │   ├── "Nothing to save." → 结束
            │   ├── skill_manage(action="create") → 创建新 skill
            │   ├── skill_manage(action="patch")  → 修复已有 skill
            │   └── skill_manage(action="write_file") → 添加参考文档
            │
            └── 显示给用户："📚 Skill created: python-debugging"
                （只是个简短总结，不打断主对话）

数天后
    │
    └── Curator 后台运行
        ├── 标记 stale skills (>30天未使用)
        ├── 归档 dead skills (>90天未使用)
        ├── 合并重叠 skills
        └── 备份全部 skills

