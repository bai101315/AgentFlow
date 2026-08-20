# 输入输出
## 输入
LLM 看到的工具 schema 有两个模式：
单任务模式：
```json
{
    "goal": "Research GRPO papers and write summary to /tmp/grpo.md",
    "context": "Focus on papers from 2024-2025. User is writing a survey.",
    "toolsets": ["web", "terminal", "file"],
    "role": "leaf"
}
```

批量并行模式：
```json
{
    "tasks": [
    {"goal": "Research frontend frameworks", "toolsets": ["web"]},
    {"goal": "Research backend frameworks", "toolsets": ["web"]},
    {"goal": "Compare database options", "toolsets": ["web", "terminal"]}
    ]
}
```

## 创建 —— _build_child_agent()
delegate_tool.py 第 1106-1174 行。创建的是一个全新的 AIAgent 实例，和主 agent 是独立的 Python 对象：

```
child = AIAgent(
    model=parent_agent.model,          # 默认继承主 agent 的模型
    provider=parent_agent.provider,
    max_iterations=50,                 # 独立的迭代限制
    enabled_toolsets=child_toolsets,   # 受限的工具集
    quiet_mode=True,                   # 静默，不污染主 agent 输出
    ephemeral_system_prompt="You are a focused subagent...",  # 独立的 system prompt
    skip_context_files=True,           # 不加载 AGENTS.md
    skip_memory=True,                  # 不访问记忆 (MEMORY.md)
    clarify_callback=None,             # 不能反问用户
    parent_session_id=parent.session_id,
)
child._delegate_depth = parent._delegate_depth + 1  # 深度 +1
```

## 执行 —— _run_single_child()

delegate_tool.py 第 1321 行。直接调用子 agent 的 run_conversation()：
```python
def _run_single_child(task_index, goal, child, parent_agent):
    # 心跳线程：每30秒通知父agent "子agent还在干活"
    # 防止 gateway 的 inactivity timeout 杀掉父进程
    
    # 实际执行
    result = child.run_conversation(goal)  # ← 同步阻塞，等子agent完成
    
    return {
        "task_index": task_index,
        "final_response": result["final_response"],
        "api_calls": result["api_calls"],
        "success": True/False,
    }
```
## 返回 —— 只返回摘要
delegate_tool.py 文件头注释（第 15-17 行）：
```
"The parent's context only sees the delegation call and the summary result, never the child's intermediate tool calls or reasoning."
```
主 agent 上下文里只会追加一条 tool result，内容类似：
```json
{
    "results": [
    {
        "task_index": 0,
        "goal": "Research GRPO papers...",
        "final_response": "I found 12 relevant papers. Key findings: ...",
        "api_calls": 8,
        "success": true
    }
    ]
}
```

# 限制机制

## 1，工具黑名单
```python
delegate_tool.py 第 45-53 行
DELEGATE_BLOCKED_TOOLS = frozenset([
    "delegate_task",   # 不能再委托（leaf 模式）
    "clarify",         # 不能反问用户
    "memory",          # 不能写 MEMORY.md
    "send_message",    # 不能发消息
    "execute_code",    # 不能写 Python 脚本（应逐步推理）
])
```

## 2,工具集不能超过父 agent
```python
第 946-949 行
if toolsets:
    expanded_parent = _expand_parent_toolsets(parent_toolsets)
    child_toolsets = [t for t in toolsets if t in expanded_parent]
    # 子 agent 的工具集 = LLM请求的 ∩ 父agent实际有的


如果父 agent 没配 browser 工具，子 agent 无论怎么请求都拿不到。
```

## 3,并发数上限
```
python
第 2009-2016 行
if len(tasks) > max_children:
    return tool_error(
        f"Too many tasks: {len(tasks)} provided, "
        f"but max_concurrent_children is {max_children}."
    )

yaml
delegation:
    max_concurrent_children: 3    # 同一批最多 3 个
    max_iterations: 50            # 每个子 agent 最多 50 轮
    child_timeout_seconds: 600    # 10 分钟超时
```
## 4, 深度限制 —— 防止递归爆炸
```python
第 1960-1972 行
depth = parent_agent._delegate_depth
if depth >= max_spawn:
    return error("Delegation depth limit reached")

你当前的配置：
yaml
delegation:
    max_spawn_depth: 1        # 只能委托一层：parent → child，child 不能再委托
    orchestrator_enabled: true # 可以开启 orchestrator 角色
```

## 5, 子 agent 危险命令自动拒绝
```python
第 73-84 行
def _subagent_auto_deny(command, description):
    """Auto-deny dangerous commands in subagent threads."""
    logger.warning("Subagent auto-denied dangerous command: %s", command)
    return "deny"
```
子 agent 跑在后台线程中，没有交互终端。如果它的 terminal 触发了危险命令审批，不会弹窗问你，而是自动拒绝。这是防止死锁（子 agent 等待你输入 y/n，但你看不到弹窗）。

你可以通过配置改为自动放行：
yaml
delegation:
    subagent_auto_approve: true    # 危险！子agent的危险命令直接执行

## 6：全局暂停开关

```python
    第 1949 行
    if is_spawn_paused():
        return tool_error("Delegation spawning is paused.")
```

    主 agent 调用 delegate_task
      │
      ├── 检查深度限制 (max_spawn_depth)
      ├── 检查并发数限制 (max_concurrent_children)
      ├── 检查全局暂停标志
      │
      ├── 对每个任务:
      │   ├── _build_child_agent()
      │   │   ├── 新建 AIAgent(同model, 受限toolsets, skip_memory, skip_context_files)
      │   │   ├── 工具集 = 请求的 ∩ 父agent的 - 黑名单
      │   │   ├── system_prompt = "You are a focused subagent..."
      │   │   └── 注册到父agent的 _active_children 列表
      │   │
      │   └── _run_single_child() [在独立线程中]
      │       ├── 启动心跳(每30秒touch父agent的activity)
      │       ├── child.run_conversation(goal)
      │       │   └── 最多 50 轮工具调用
      │       │   └── 超时 600 秒
      │       │   └── 危险命令 auto_deny
      │       ├── 收集 final_response + api_calls
      │       └── 注销子agent
      │
      └── 汇总所有结果
          └── 返回 tool result JSON 给主 agent

    和主 agent 的隔离程度
    
    | 维度 | 共享？ | 说明 |
    |------|--------|------|
    | LLM 模型 | 是 | 默认用同一模型，可配置不同 |
    | API key | 是 | 继承父 agent 的凭证 |
    | 文件系统 | 是 | 同一个文件系统，但独立 task_id |
    | 终端会话 | 否 | 独立 task_id，独立 shell 状态 |
    | 上下文历史 | 否 | 子 agent 不知道主 agent 聊了什么 |
    | 记忆 (MEMORY.md) | 否 | skip_memory=True |
    | 项目上下文 (AGENTS.md) | 否 | skip_context_files=True |
    | 工具集 | 子集 | 受限于父 agent + 黑名单过滤 |
    | Prompt Cache | 否 | 子 agent 重新构建 system prompt |
    | 迭代预算 | 独立 | 各 50 轮，互不影响 |


# 何时调用

答案很反直觉——判定权完全在 LLM，Hermes 没有硬编码的「何时调用子 agent」逻辑。

Hermes 把一个长长的"使用指南"嵌入在 delegate_task 工具的 description 字段里，LLM 调用前看到的就是这段话。它是 LLM 唯一的判定依据。

## LLM 实际看到的（动态生成）
每次构建 tool schema 时，_build_top_level_description() 动态生成描述文本。你当前配置下，LLM 看到的 delegate_task 描述核心部分是：

```
WHEN TO USE delegate_task:
- Reasoning-heavy subtasks (debugging, code review, research synthesis)
- Tasks that would flood your context with intermediate data
- Parallel independent workstreams (research A and B simultaneously)

WHEN NOT TO USE (use these instead):
- Mechanical multi-step work with no reasoning needed -> use execute_code
- Single tool call -> just call the tool directly
- Tasks needing user interaction -> subagents cannot use clarify
- Durable long-running work that must outlive the current turn -> 
    use cronjob or terminal(background=True)
```

这就是全部判定逻辑。 没有代码检查"用户是不是说了简单问题"，没有阈值判断"消息数不到 X 就不要 fork"，没有复杂度估算——就是一段文字描述，LLM 自己看着办。

## 软约束出现的问题
1. 过度调用
"帮我写个 Hello World" → LLM 可能觉得「这是个 coding 任务，fork 个子 agent」→ 浪费一次 API 调用和 50 轮子 agent 预算。

你问「怎么防止主 agent 随意创建」。答案分几层：
```
第一层（软约束）：tool schema 里的 guidance
上面那段 WHEN NOT TO USE 就是在尽力引导 LLM 不要滥用。但 LLM 听不听是另一回事。

第二层（硬约束）：delegate_task 本身的限制
即使 LLM 调了，Hermes 在代码层面能做到的约束：

| 约束 | 实现 | 你的配置 |
|------|------|---------|
| 每次最多 3 个并发 | max_concurrent_children: 3 | ✅ |
| 每个子 agent 最多 50 轮 | max_iterations: 50 | ✅ |
| 每个子 agent 最多 10 分钟 | child_timeout_seconds: 600 | ✅ |
| 不允许嵌套委托 | max_spawn_depth: 1 | ✅ |
| 危险命令自动拒绝 | subagent_auto_approve: false | ✅ |
| 不能反问用户 | clarify 在黑名单 | ✅ |

这些是硬约束。 即使 LLM 疯狂调用 delegate_task，每次调用也只能产生最多 3 个并发子 agent，每个跑不过 50 轮、10 分钟。主 agent 自己的 90 轮上限还在，不会被子 agent 的消耗影响（子 agent 有独立预算）。

第四层（可选）：全局暂停开关
如果你发现 LLM 在滥用，可以通过 TUI 的 /agents 面板按 p 暂停所有新 spawn，或者直接禁用 toolset：

bash
hermes tools disable delegation
```

2. LLM 可能不够调
复杂任务反过来——该调的时候不调，自己吭哧吭哧 90 轮工具调用把上下文撑爆。

这个问题没有代码层面的解决方案。唯一的办法是在 system prompt 或 skill 中明确引导。这就是为什么 Hermes 的 skill 系统里会有 subagent-driven-development 之类的技能——当你的任务匹配时自动加载，告诉 LLM「这种任务应该拆分子 agent」。

| | Hermes | deer-flow |
|---|---|---|
| 谁决定拆分子任务 | LLM 自主判断（看 tool description） | LangGraph 图结构预先定义 |
| 有硬编码判定逻辑吗 | 没有 | 有（planning agent → task decomposition → worker dispatch） |
| 如何约束 | tool description 软引导 + 并发/时间硬上限 | graph 节点 + conditional edges |
| 何时拆分子任务 | LLM 认为"需要"就拆 | 由 LangGraph 的 Supervisor/Lead agent 规划后拆 |
| 简单问题会调用吗 | 可能，看 LLM 判断 | 图结构保证了简单路径不走 plan→dispatch |

这不是漏洞，是一种权衡。硬编码「何时该用 sub-agent」的规则在 2024 年的 agent 项目里被证明是脆弱且不可维护的——今天你觉得"10 次工具调用就该 fork"，下个月 DeepSeek V5 出来了，它在 20 次调用时才需要 fork。所以 Hermes 选择把判断权交给 LLM，自己只设硬上限。
