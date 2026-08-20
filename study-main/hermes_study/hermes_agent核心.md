# 保留

├── run_agent.py          # AIAgent 类 — 构造 + run_conversation() 入口
├── model_tools.py        # handle_function_call() + get_tool_definitions()
├── toolsets.py           # toolset 定义列表
├── agent/                # 核心逻辑
│   ├── agent_init.py     # init_agent() — 串联 provider/tools/guardrails/memory
│   ├── conversation_loop.py  # 真正的 while 循环（4000行那个）
│   ├── process_bootstrap.py  # OpenAI 客户端 + SafeWriter
│   └── prompt_builder.py     # 系统提示词构建（如果要保留 skills/memory）
└── tools/                # 工具实现
    ├── registry.py       # 工具注册中心
    └── *.py              # 各个工具文件（根据需要裁剪）

# 要删的
- cli.py — 交互式 CLI
- hermes_cli/ — 子命令、slash command
- gateway/ — 所有 messaging 平台
- tui_gateway/, ui-tui/ — TUI
- acp_adapter/ — IDE 集成
- cron/ — 定时任务
- plugins/ — 如果你不需要外部 memory provider 的话
- skills/, optional-skills/ — 如果你不需要 skill 系统的话
- hermes_state.py — SessionDB（如果你不需要跨会话持久化）
- batch_runner.py — 批量跑

# 最小调用

```python
from run_agent import AIAgent

agent = AIAgent(
    base_url="https://api.deepseek.com",
    api_key="sk-xxx",
    model="deepseek-chat",
    max_iterations=30,
    enabled_toolsets=["terminal", "file", "web"],
    skip_memory=True,              # 不需要记忆系统
    skip_context_files=True,       # 不需要 AGENTS.md
    quiet_mode=True,
)

response = agent.chat("帮我写一个 Python 排序脚本")
print(response)
```
