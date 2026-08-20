# 执行终端命令

# 💻 preparing terminal… 是什么？

只是一个 UI 通知。当 LLM 决定调用 terminal 工具并开始生成参数时，CLI 回调被触发

它出现的意思是："LLM 决定执行终端命令了，正在准备参数"。类似地，你也会看到：

┊ 📖 preparing read_file…
┊ ✍️ preparing write_file…
┊ 🔍 preparing search_files…
┊ 🔀 preparing delegate_task…
┊ 🧠 preparing memory…

# 读写文件、执行命令 都是主agent执行
主 Agent 的 while 循环
    │
    ├─ LLM 返回 tool_calls: ["terminal", "read_file", "write_file"]
    │
    ├─ handle_function_call("terminal", {"command": "ls"})
    │     └─ subprocess.Popen("ls", shell=True, ...)    ← 直接在本机执行
    │
    ├─ handle_function_call("read_file", {"path": "/tmp/x.py"})
    │     └─ Path("/tmp/x.py").read_text()              ← 直接读本地文件
    │
    ├─ handle_function_call("write_file", {"path": "/tmp/y.py", "content": "..."})
    │     └─ Path("/tmp/y.py").write_text("...")         ← 直接写本地文件
    │
    └─ 工具结果追加到 messages → 继续循环

read_file、write_file、patch、search_files、terminal —— 全部是同步工具调用，在主进程中直接执行。


| | delegate_task（子 agent） | execute_code（sandbox） |
|---|---|---|
| 本质 | 全新的 AIAgent 实例 | 一个 Python 子进程 |
| LLM 参与 | 有自己的 LLM 调用循环 | 只执行你写的 Python 脚本 |
| 工具访问 | 受限的 toolset（白名单） | 仅 7 个白名单工具（通过 RPC） |
| 上下文 | 不知道主 agent 聊了什么 | 只能看到你传给它的数据 |
| 用途 | 复杂的独立推理任务 | 批量工具调用、数据处理 |


# 创建子agent的触发条件
在prompt里面进行约束

WHEN TO USE delegate_task:
- Reasoning-heavy subtasks (debugging, code review, research synthesis)
- Tasks that would flood your context with intermediate data
- Parallel independent workstreams (research A and B simultaneously)

WHEN NOT TO USE:
- Mechanical multi-step work with no reasoning needed -> use execute_code
- Single tool call -> just call the tool directly
- Tasks needing user interaction -> subagents cannot use clarify

作用：解决三个工程问题
- 上下文隔离 —— 防止 LLM 被中间数据淹没
- 并行处理 —— 真正的同时工作
- 独立工具上下文 —— 互不干扰

每个子 agent 有独立的 shell session、独立的文件操作缓存、独立的工具集。不会互相覆盖文件。
