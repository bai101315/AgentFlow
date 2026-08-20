# 三层观察架构

# 日志文件 — 实时文本记录

~/.hermes/logs/ 下三个自动滚动的日志：

| 文件 | 级别 | 内容 |
|------|------|------|
| agent.log | INFO+ | 一切：插件加载、API 调用、工具执行、压缩、重试 |
| errors.log | WARNING+ | 只看错误和警告 |
| gateway.log | INFO+ | 仅 gateway（Telegram/Discord 等消息平台） |

你的 agent.log 目前 716 行，88KB。每条记录长这样：


2026-05-29 14:25:31,152 INFO run_agent: conversation turn: session=abc... model=deepseek-v4-pro ...
2026-05-29 14:25:35,892 INFO tools.terminal_tool: terminal executed: exit=0, cmd="ls -la", duration=0.8s
2026-05-29 14:25:42,103 WARNING agent.conversation_loop: empty response recv'd, retrying with prefill...


每条日志都会附带 [session_id] 标签，方便筛选。

查看日志的命令

bash
hermes logs                        # 最近 50 行 agent.log
hermes logs -f                     # 实时跟踪（类似 tail -f）
hermes logs errors                 # 只看 errors.log
hermes logs --level WARNING        # 只看 WARNING 及以上
hermes logs --session abc123       # 按 session ID 筛选
hermes logs --component tools      # 只看 tools.* 的日志
hermes logs --since 1h             # 最近 1 小时
hermes logs --since 30m -f         # 最近 30 分钟 + 实时跟踪


日志文件配置：

yaml
logging:
    level: INFO          # DEBUG / INFO / WARNING / ERROR
    max_size_mb: 5       # 单个文件最大 5MB，超限自动轮转
    backup_count: 3      # 保留 3 个旧文件

# 会话文件 — 完整对话 JSON 追踪

 ~/.hermes/sessions/ 下每个 session 一个 JSON 文件。包含了每一轮 LLM 看到的东西、调用了什么工具、思考了什么（reasoning）。
    
这是最详细的记录。你当前会话的追踪格式：

json
[
    {
    "role": "user",
    "content": "你现在能看到.hermes这个目录吗？"
    },
    {
    "role": "assistant",
    "reasoning": "The user is asking me to look at their ~/.hermes directory...",
    "tool_calls": [
        {"function": {"name": "skill_view", "arguments": "{\"name\":\"hermes-agent\"}"}},
        {"function": {"name": "search_files", "arguments": "{\"pattern\":\"*\",\"path\":...}"}}
    ]
    },
    {
    "role": "tool",
    "tool_call_id": "call_xxx",
    "content": "{\"success\": true, \"name\": \"hermes-agent\", ...}"
    },
    ...
]


关键字段：

| 字段 | 含义 |
|------|------|
| role: "assistant" + tool_calls | LLM 决定调用哪些工具 |
| role: "assistant" + reasoning | LLM 的思考过程（如果你开了 reasoning） |
| role: "tool" | 工具执行结果（完整 JSON） |
| role: "assistant" + content (text) | 最终文本回复 |

查看会话的命令

bash
hermes sessions list           # 列出所有会话
hermes sessions browse         # 交互式选择
hermes sessions export OUT     # 导出为 JSONL
hermes sessions stats          # 会话统计
hermes sessions rename ID T    # 重命名

# CLI 实时显示 — 你直接能看到的信息

在终端对话中就有实时反馈。

工具调用进度

当前配置 tool_progress: all，每次工具调用会显示：

🧠 memory                          ← 工具名 + emoji
┊                                  ← 工具结果（截断版本）

配置选项：

yaml
display:
    tool_progress: all       # all | new | verbose | off


思考过程（Reasoning）

如果你配置 display.show_reasoning: true，LLM 的推理过程会在回答前展示：

yaml
display:
    show_reasoning: true      # 展示 DeepSeek V4 的 reasoning 内容


或者在会话中随时切换：

/reasoning show      # 显示推理
/reasoning hide      # 隐藏
/reasoning xhigh     # 要求模型深入思考

执行流程摘要

每次 API 调用时 CLI 会打印：

🔄 Making API call #1/90...
    📊 Request size: 18 messages, ~42,000 tokens
    🔧 Available tools: 45

# hermes insights — 用量统计

bash
hermes insights              # 最近 7 天
hermes insights --days 30    # 最近 30 天

输出类似：

Total sessions:      46
Total API calls:     842
Total tokens in:     3,240,000
Total tokens out:      186,000
Estimated cost:       $4.82
Average turns/session: 18.3
Most used tools:
terminal         342
read_file        218
search_files     156
memory            49
delegate_task     23



你当前会话的实际例子

你的 session_20260529_142443 有 397 条消息，其中可以看到我来回 26 次调用了 memory 工具。回顾一下流程：

[0] user       | "你现在能看到.hermes这个目录吗？"
[1] assistant  | reasoning: "The user is asking..."    ← 思考
                    tool_calls: [skill_view, search_files] ← 决定调用两个工具
[2] tool       | skill_view 结果 (JSON)                 ← 工具1 执行结果
[3] tool       | search_files 结果 (JSON)               ← 工具2 执行结果
[4] assistant  | reasoning: "..." 
                    tool_calls: [search_files, ...]       ← 看到结果后继续调用
    ...


完整可观测性速查

| 你想知道 | 怎么看 |
|----------|--------|
| Agent 调了哪些工具 | hermes logs --component tools 或看 session JSON |
| Agent 思考了什么 | /reasoning show 或看 session JSON 里的 reasoning 字段 |
| API 调用耗时 | agent.log 中 duration= |
| 花了多少钱 | hermes insights |
| 工具执行结果 | session JSON 中 role: "tool" 的消息 |
| 重试/错误 | hermes logs errors |
| 实时执行流程 | hermes logs -f |
| 某次会话的完整轨迹 | ~/.hermes/sessions/session_xxx.json |

