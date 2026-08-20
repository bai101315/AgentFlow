# Hermes 可以读写删本地文件
能。完全能。 就在你的 WSL 本地文件系统上操作。

```python
# 这就是 LLM 调用 read_file("/home/bai/test.py") 时实际执行的代码
def _handle_read_file(args):
    path = args["path"]
    content = Path(path).read_text(encoding="utf-8")  # ← 直接读你本地的文件
    return json.dumps({"content": content, "total_lines": N})

#  写文件
def _handle_write_file(args):
    path = args["path"]
    content = args["content"]
    Path(path).write_text(content, encoding="utf-8")  # ← 直接覆盖你本地的文件
    return json.dumps({"success": True})

# 修改文件（patch）

def _handle_patch(args):
    path = args["path"]
    old = args["old_string"]
    new = args["new_string"]
    content = Path(path).read_text()
    content = content.replace(old, new)    # ← 直接修改
    Path(path).write_text(content)

# 搜索结果
search_files 底层调的是 ripgrep (rg)，在你本地文件系统上搜
subprocess.run(["rg", "--json", pattern, path])

```

# 在哪里执行命令？

在你的 WSL 本地环境。 就是 subprocess.Popen(your_command, shell=True)。

```python
def terminal(command, timeout=None, workdir=None):
    proc = subprocess.Popen(
        command,
        shell=True,              # ← 就是普通的 shell 执行
        cwd=workdir,             # 工作目录
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = proc.communicate(timeout=timeout)
    return json.dumps({
        "output": stdout + stderr,
        "exit_code": proc.returncode,
    })


ls、pwd、pip install、git commit、python train.py——全部跑在你的 WSL 里。
```
你的配置就是本地模式：

```yaml
terminal:
    backend: local    # ← 没有 Docker，没有 SSH，没有 Modal，就是本机
    cwd: .            # 工作目录 = 当前目录
```

# 安全防护

## 危险命令审批

terminal_tool.py 在执行前会调用 tirith 安全扫描器。如果命令包含危险模式（rm -rf /、git reset --hard 等），会暂停并问你确认。

## 工作目录约束

terminal 默认 cwd: .（当前目录）。如果 LLM 尝试 rm -rf /，审批机制会拦截。你可以配置固定工作目录来进一步限制。

## 文件读取大小限制
file_read_max_chars: 100000   # 单次最多读 100K 字符
防止 LLM 一次性吞掉超大文件撑爆上下文。

## execute_code sandbox（之前讲的）
这是唯一真正隔离的环境。当你用 execute_code（让 LLM 写 Python 脚本时），脚本跑在子进程中，环境变量被剥离（没有 API keys），工具被限白名单。

| | Hermes (local) | Claude Code | Cursor |
|---|---|---|---|
| 读文件 | ✅ 直接读 | ✅ | ✅ |
| 写文件 | ✅ 直接写 | ✅ | ✅ |
| 删除文件 | ✅ terminal(rm) | ✅ | ✅ |
| 命令在哪跑 | 你的 WSL 本机 | 你的本机 | 你的本机 |
| 有没有 sandbox | execute_code 有，terminal 没有 | 有审批 | 有审批 |
| 危险命令 | 弹确认 (y/n) | 弹确认 | 弹确认 |
    
# Hermes的Sandbox机制

核心：子进程 + RPC 代理

execute_code 本质上是一个受限的 Python 子进程，它在被精心裁剪的环境中运行 LLM 生成的脚本。脚本不能直接调 Hermes 工具——必须通过 RPC 隧道。

    
    ┌── 父进程 (Hermes Agent) ──────────────────────┐
    │                                                │
    │  启动 RPC Server 线程                           │
    │  ├── Unix Domain Socket (Linux/macOS)          │
    │  └── TCP 127.0.0.1:随机端口 (Windows)          │
    │                                                │
    │  → 接收子进程的 tool call 请求                  │
    │  → 在父进程中执行真实工具 (terminal/search/...)  │
    │  → 返回 JSON 结果                               │
    └──────────────────┬─────────────────────────────┘
                       │ RPC (UDS/TCP)
    ┌── 子进程 (Sandbox) ───────────────────────────┐
    │                                                │
    │  环境变量被剥离 (无 API keys)                   │
    │  from hermes_tools import terminal, web_search │
    │  result = terminal("ls -la")                   │
    │     ↓                                          │
    │  通过 RPC 发送到父进程的真正 terminal() 执行     │
    │                                                │
    │  超时? → SIGKILL 整个进程组                     │
    │  工具调用 > 50? → 强制终止                      │
    │  stdout > 50KB? → 截断 (40%头 + 60%尾)          │
    └────────────────────────────────────────────────┘

可用工具白名单，子进程只能调 7 个工具：
```python
code_execution_tool.py 第 60-68 行
SANDBOX_ALLOWED_TOOLS = frozenset([
    "web_search",    # 网页搜索
    "web_extract",   # 网页内容提取
    "read_file",     # 读文件
    "write_file",    # 写文件
    "search_files",  # 搜索文件
    "patch",         # 文件修改
    "terminal",      # 终端命令
])
``` 
不能调: delegate_task, memory, browser_, skill_, clarify, cronjob 等等。

## RPC协议
子进程中的代码长这样（Hermes 自动生成的 hermes_tools.py stub）：

```python
子进程看到的 hermes_tools.py 内容:

import json, socket, os

_sock = None

def _connect():
    endpoint = os.environ["HERMES_RPC_SOCKET"]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(endpoint)          # 连接到父进程的 UDS
    return sock

def _call(tool_name, args):
    request = json.dumps({"tool": tool_name, "args": args}) + "\n"
    conn = _connect()
    conn.sendall(request.encode())  # 发送: {"tool":"terminal","args":{"command":"ls"}}
    buf = b""
    while True:
        chunk = conn.recv(65536)
        buf += chunk
        if buf.endswith(b"\n"):
            break
    return json.loads(buf.decode()) # 收到: {"output":"...","exit_code":0}

然后用简单的函数封装:
def terminal(command, timeout=None, workdir=None):
    """Run a shell command."""
    return _call("terminal", {"command": command, "timeout": timeout, "workdir": workdir})

def web_search(query, limit=5):
    return _call("web_search", {"query": query, "limit": limit})

... 其他工具同理


LLM 写的脚本直接 from hermes_tools import terminal 然后像正常 Python 函数一样调用，但底层数据通过 socket 传回父进程真正执行。
```    

## 与deerflow对比

                                    
      终端 CLI 进程                    Web 服务 + Docker
      ┌─────────────┐               ┌──────────────────────┐
      │ agent 进程    │              │ nginx 反向代理         │
      │ 直接操作本地   │              │   ↓                   │
      │ 文件系统      │              │ Gateway API 服务       │
      │ subprocess   │              │   ↓                   │
      │ 执行 shell   │              │ LangGraph Agent 循环   │
      └─────────────┘              │   ↓                   │
                                    │ Sandbox 容器 (Docker)  │
      危险命令 → 弹确认              │   隔离的文件系统        │
      无容器隔离                    │   隔离的 shell 执行     │
                                    └──────────────────────┘

详细对比：
    
    | | Hermes | deer-flow |
    |---|---|---|
    | 运行方式 | 本地 Python 进程，直接 CLI | Web 服务器 + Docker 容器 |
    | 文件读写 | 直接读写宿主机文件 | 映射到 /mnt/user-data/ 容器内路径 |
    | 命令执行 | subprocess.Popen 在宿主机跑 | Docker 容器内跑，默认禁用 bash |
    | 隔离机制 | 无（仅审批弹窗） | Docker 容器 + 每线程独立目录 |
    | 默认安全 | 宽松（approvals: manual） | 严格（local 模式默认禁用 bash） |
    | API keys | 在本地 .env，agent 可见 | 通过环境变量注入，容器内可见 |
    | 网络暴露 | 无（纯 CLI） | 有（Web UI + IM channels），默认 localhost |
    | 安全警告 | 无 | 明确警告不要部署到公网 |
    | 底层框架 | 裸 Python | LangChain + LangGraph |

deerflow会严格很多：
- Docker 模式（推荐）：agent 跑在容器里，文件系统和 shell 都是隔离的。删文件删的是容器里的，主机不受影响。
- Local 模式（本地开发）：文件映射到每线程独立目录，bash 默认关闭。需要手动启用。
- 安全警告：明确告诉用户「不要部署到局域网或公网，除非你配了 IP 白名单、认证网关、网络隔离」。

## 设计理念不同
Hermes 为什么不做容器隔离？

设计理念不同。Hermes 是个人开发者工具，不是服务。它假设：

1. 你在自己的电脑上用
2. 你是唯一用户
3. 危险操作弹确认就够了

所以 Hermes 的安全边界是审批弹窗 + 你的判断，不是容器。

deer-flow 是多用户 Web 服务，必须假设可能有恶意用户、可能有网络攻击，所以必须用 Docker 容器+网络隔离+认证网关。


    | 你的风险问题 | Hermes 的情况 | deer-flow 的做法 |
    |---|---|---|
    | Agent 能否删我本地文件？ | 能，rm -rf 会弹确认但可能放行 | Docker 模式：删的是容器内；Local 模式：bash 默认禁用 |
    | Agent 能否读我的密钥？ | 能，.env 文件可读 | 同样能读容器内环境变量，但文件系统隔离 |
    | Agent 能否执行危险命令？ | 能，审批弹窗是唯一防线 | Docker 隔离；Local 模式默认禁用 bash |
    | Agent 被网络攻击利用？ | 无网络暴露（纯 CLI） | 有风险，README 明确警告 |


