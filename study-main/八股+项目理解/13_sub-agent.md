# 触发条件
它本质上是一个 LangChain tool。主 agent 只有在工具列表里拿到了这个 task 工具，才可能触发 sub-agent。

```subagent_enabled=True```进行控制，主要是使用```task_tool```是作为TOOL交给主agent进行实现得

软约束：在prompt里面进行控制，模型自己判断是否进行拆分

# 输入：
```python
async def task_tool(
    runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id,
    max_turns: int | None = None,
) -> str:

对 LLM 来说，它主要要填 4 个参数：

description：用于记录/显示的任务简短描述（3-5 个字）。请务必首先提供此参数。
prompt：子代理的任务描述。请具体、清晰地说明需要完成的任务。请务必其次提供此参数。
subagent_type：要使用的子代理类型。请务必其次提供此参数。目前主要是 "general-purpose" 或 "bash"。
max_turns：可选的代理最大回合数。默认为子代理配置的最大回合数。
```

例子：
```
task(
    description="Inspect auth code",
    prompt="Read the authentication-related files, identify login flow, token handling, and possible security risks. Return key findings with file paths.",
    subagent_type="general-purpose"
)
```

# sub-agent 类型
## general-purpose
```
GENERAL_PURPOSE_CONFIG = SubagentConfig(
    name="general-purpose",
    description="",
    system_prompt="",
    tools=None,  # Inherit all tools from parent
    disallowed_tools=["task", "ask_clarification", "present_files"],  # Prevent nesting and clarification
    model="inherit",
    max_turns=10,
)
```

## bash
```
BASH_AGENT_CONFIG  = SubagentConfig(
    name="bash",
    description="",
    system_prompt="",
    tools=["bash", "ls", "read_file", "write_file", "str_replace"],  # Sandbox tools only
    disallowed_tools=["task", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=60,
)
```

# sub-agent 能拿到什么环境

当主 agent 调用 task 工具时，task_tool 会从 runtime 里取父 agent 的上下文：
```
sandbox_state = runtime.state.get("sandbox")
thread_data = runtime.state.get("thread_data")
thread_id = runtime.context.get("thread_id") ...
parent_model = metadata.get("model_name")
trace_id = metadata.get("trace_id") or ...

sandbox_state:
  当前沙箱状态，比如 sandbox_id。

thread_data:
  当前线程的数据目录信息，例如 workspace、uploads、outputs。

thread_id:
  当前对话线程 id，给 sandbox/middleware/checkpoint 等使用。

parent_model:
    主 agent 当前使用的模型名。
    如果 subagent config 里 model="inherit"，子 agent 会用这个模型。

trace_id:
  日志追踪用，让 parent/subagent 的日志能串起来。
```

子agent的初始消息：
```
state = {
    "messages": [HumanMessage(content=task)],
}
```
这里的 task 就是主 LLM 调用 task_tool 时传入的 prompt。

所以sub-agent几乎什么都拿不到
```
子 agent 知道什么，主要取决于主 agent 写给它的 prompt。
环境目录/沙箱/thread 会继承，但语义上下文不会自动完整继承。
```

general-purpose:
  继承大多数工具
  但移除 task / ask_clarification / present_files

bash:
  只保留 bash / ls / read_file / write_file / str_replace

# sub-agent 是怎么被创建出来的
真正创建子 agent 的地方在：

```python
def _create_agent(self):
    model_name = _get_model_name(self.config, self.parent_model)
    model = create_chat_model(name=model_name, thinking_enabled=False)

    middlewares = build_subagent_runtime_middlewares(lazy_init=True)

    return create_agent(
        model=model,
        tools=self.tools,
        middleware=middlewares,
        system_prompt=self.config.system_prompt,
        state_schema=ThreadState,
    )
```

几个重点：
```
1. 子 agent 也是 LangChain create_agent 创建出来的
2. 子 agent 有自己的 system_prompt
3. 子 agent 有自己的工具列表
4. 子 agent 也挂 runtime middlewares
5. 子 agent 使用 ThreadState
6. thinking_enabled=False
```

# 后台执行流程
task_tool 并不是同步直接跑完子 agent，而是：

```
task_id = executor.execute_async(prompt, task_id=tool_call_id)
```

execute_async() 在 executor.py (line 503)。
```
1. 创建一个 SubagentResult
2. 放进全局 _background_tasks
3. 提交到 _scheduler_pool
4. scheduler 再提交真实执行到 _execution_pool
5. 用 timeout 等待执行完成
```
所以子 agent 是后台线程运行的，最多并发大致受这些线程池限制。

这表示子 agent 是流式执行的。每次有新的状态 chunk，就检查最后一条消息是不是 AIMessage，如果是，就收集起来：

# task_tool 怎么等待子 agent 完成

虽然子 agent 是后台执行的，但 task_tool 会阻塞等待它完成。
```python
while True:
    result = get_background_task_result(task_id)
    ...
    await asyncio.sleep(5)
```

# sub-agent 最终怎么生成返回值

子 agent 执行结束后，SubagentExecutor 会从最终 state 里找最后一条 AIMessage, 返回最后一条信息：message(字符串形式)

```
last_ai_message = None
for msg in reversed(messages):
    if isinstance(msg, AIMessage):
        last_ai_message = msg
        break

result.result = content
```

# 取消机制
如果父级 task_tool 被取消，会捕获：
```
except asyncio.CancelledError:

request_cancel_background_task(task_id)

result.cancel_event.set()

子 agent 在 agent.astream() 每次迭代边界检查：
if result.cancel_event.is_set():
    result.status = SubagentStatus.CANCELLED
    return result
```

所以这是 协作式取消。如果子 agent 卡在某个长时间工具调用里，不一定能立刻停，要等下一次 stream chunk 边界。
