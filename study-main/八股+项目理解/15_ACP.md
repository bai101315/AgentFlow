
ACP 在这里指 Agent Client Protocol，也就是“客户端和外部 Agent 之间通信的协议”。

DeerFlow 主 Agent 想把一个任务交给另一个会写代码/会执行任务的 Agent，例如 codex、claude_code，就通过 ACP 这个标准协议启动它、发任务、接收过程更新和最终结果。

# MCP 和 ACP
```
MCP：让 Agent 调用外部工具、资源、服务，比如 LeetCode、GitHub、搜索、数据库。
ACP：让一个客户端应用调用另一个“完整 Agent”。它不是“工具服务器”，而更像“把任务委托给另一个 AI 助手”。
```

# 协议角色
```
Client：客户端。你的项目 DeerFlow 就是 Client，负责启动外部 Agent、给它任务、接收它的输出、处理权限请求。
Agent：被调用的外部智能体。比如一个支持 ACP 的 Codex/Claude Code 进程。
```
通信通常走 stdio + JSON-RPC：DeerFlow 启动一个子进程，然后通过它的标准输入/标准输出传 JSON-RPC 消息。

你本地装的包是 agent-client-protocol==0.9.0，包目录是 .venv/Lib/site-packages/acp。它的元信息里说明这是 Zed Industries 的 ACP Python SDK。

# 协议方法

本地 SDK 里定义的 Agent 侧主要方法在 ```.venv/Lib/site-packages/acp/meta.py```：
```text
initialize
session/new
session/load
session/list
session/prompt
session/cancel
session/close
session/set_model
session/set_mode
authenticate
```

client侧主要方法：
```
session/update
session/request_permission
fs/read_text_file
fs/write_text_file
terminal/create
terminal/output
terminal/wait_for_exit
terminal/kill
```

# 配置入口

ACP 的配置入口是 config.yaml 里的 acp_agents 字段。代码用这个模型接收：

```python
class ACPAgentConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    description: str
    model: str | None = None
    auto_approve_permissions: bool = False

command：启动 ACP Agent 的命令，比如 codex、claude、某个 Python 脚本。
args：命令参数。
env：传给子进程的环境变量。
description：告诉主 Agent 这个外部 Agent 擅长什么。
model：可选模型提示。
auto_approve_permissions：外部 Agent 请求权限时是否自动批准。

比如：
acp_agents:
  codex:
    command: codex
    args: ["--acp"]
    env:
      OPENAI_API_KEY: "$OPENAI_API_KEY"
    description: "Use Codex for code editing, debugging, and repository analysis."
    model: "gpt-5"
    auto_approve_permissions: false
```

# 加载过程
1. 代码调用 get_app_config()
2. get_app_config() 会找 config.yaml，如果文件有变化会重新加载。
3. AppConfig.from_file() 读取 YAML。
4. 它解析环境变量，比如 $OPENAI_API_KEY。
5. ```load_acp_config_from_dict(config_data.get("acp_agents", {}))```
6. load_acp_config_from_dict() 把配置转成全局 _acp_agents 字典。

```python
_acp_agents = {
    name: ACPAgentConfig(**cfg)
    for name, cfg in config_dict.items()
}
```

# 沙箱工作区
```
/mnt/acp-workspace
{base_dir}/threads/{thread_id}/acp-workspace/
```

# 调用过程
1. 主 Agent 决定需要外部 Agent。
2. 调用 invoke_acp_agent(agent_name="codex", prompt="...")
3. 工具读取 get_acp_agents()
4. 找到对应配置。
5. 创建 thread 专属 ACP workspace。
6. 用 ACP SDK 启动子进程。
```python
.venv/Lib/site-packages/acp/stdio.py
spawn_agent_process(...)
command + args
```
然后建立 ClientSideConnection。
7. Client 发送 initialize。
8. Client 发送 session/new，通常传入 cwd，这里应该是 ACP workspace。
9. Client 发送 session/prompt，把任务发给外部 Agent。
外部 Agent 通过 session/update 流式返回思考、消息、工具调用、计划、用量等。
10. 如果外部 Agent 请求文件/终端/权限，Client 处理
```
fs/read_text_file
fs/write_text_file
terminal/create
session/request_permission
```
11. 工具收集最终结果，返回给主 Agent。
12. 如果外部 Agent 生成了文件，主 Agent 从 /mnt/acp-workspace 读，再复制到 /mnt/user-data/outputs，最后用 present_file 给用户。


# stdio 实现

stdio就是用子进程的：
```
stdin  标准输入
stdout 标准输出
stderr 标准错误

父进程 DeerFlow  -> 写 JSON-RPC 到子进程 stdin
子进程 MCP Server -> 写 JSON-RPC 响应到 stdout
```


stdio 通信 = 父程序启动一个子程序，然后两边通过“输入框/输出框”偷偷传 JSON 消息。


现有代码只提供了参数，真正的底层 subprocess.Popen(...)、怎么读写 stdin/stdout，不是你项目写的，是第三方库 langchain-mcp-adapters 写的。

