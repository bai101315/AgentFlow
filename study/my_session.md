## 1——对话 session：由 thread_id 驱动

config = {
    "configurable": {
        "thread_id": "improve-thread-01",
        ...
    }
}

thread_id = "improve-thread-01" 就是这次会话的身份 ID。后面它被放进两个地方：

### 1: 
runtime = Runtime(context={"thread_id": thread_id})
config["configurable"]["__pregel_runtime"] = runtime

### 2: 以及每次调用 agent 时：
result = await agent.ainvoke(
    state,
    config=config,
    context={"thread_id": thread_id}
)

所以 LangGraph 会把所有轮次都归到同一个 thread_id 下。只要 thread_id 不变，它就是同一个会话；换一个 thread_id，就是新会话

## 2——上下文如何跨轮保存：

看起来只传了一句话，但因为 agent 创建时挂了 checkpointer：
async with make_checkpointer() as checkpointer:
    agent = make_lead_agent(config, checkpointer=checkpointer)

而 config.yaml 里配置的是 SQLite：

checkpointer:
  type: sqlite
  connection_string: checkpoints.db

所以 LangGraph 会按 thread_id 把消息状态、summary、todos、artifacts 等 ThreadState 保存到 SQLite checkpoint。项目里实际路径会解析到 .deer-flow/checkpoints.db 或 .deer_flow/checkpoints.db 这一类 DeerFlow 状态目录，日志里也显示过使用 SQLite saver。

## checkpoints.db的详细内容
.deer_flow/checkpoints.db只有两张表：
- checkpoints  642 rows
- writes       896 rows

### checkpoints-key
checkpoints 的主键(key)是： (thread_id, checkpoint_ns, checkpoint_id)
对于当前会话来说，(thread_id = improve-thread-01, checkpoint_ns = '', checkpoint_id = 每一步状态快照的 UUID)

### checkpoints-value
#### checkpoints.checkpoint 
checkpoints.checkpoint是一个序列化 BLOB，类型通常是 msgpack。解开后大概是：
{
  "v": 4,
  "ts": "2026-05-05T12:05:18.627436+00:00",
  "id": "...",
  "channel_values": {
    "messages": [...],
    "thread_data": {...},
    "artifacts": [...],
    "__pregel_tasks": [],
    ...
  },
  "channel_versions": {...},
  "versions_seen": {...},
  "updated_channels": [...]
}

重要的是 channel_values
messages       当前会话消息，包括 HumanMessage / AIMessage / ToolMessage
thread_data    当前 thread 的 workspace/uploads/outputs 路径
artifacts      Agent 产出的文件路径
todos          如果 plan mode/todo 写入过，会在这里
uploaded_files 如果有上传文件，会在这里
branch:to:*    LangGraph 内部调度通道

#### checkpoints.metadata 
metadata 是 JSON，记录这一步运行配置，比如：
{
  "source": "loop",
  "step": 164,
  "agent_name": "default",
  "model_name": "minimax-m2.5",
  "thinking_enabled": false,
  "is_plan_mode": true,
  "subagent_enabled": true,
  "tools_enabled": true
}

### writes -key

writes 表则是每个 checkpoint 过程中的“增量写入”：
key = (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
value = 某个 channel 的一次写入



简化理解：
thread_id = improve-thread-01
        ↓
agent.ainvoke(...)
        ↓
LangGraph checkpointer
        ↓
SQLite checkpoints.db
        ↓
下次同 thread_id 自动续上上下文

## 3——session 文件目录

除了 checkpoint，这个项目还按 thread_id 分配文件工作区。路径逻辑在 backend/config/paths.py (line 142)：

.deer-flow/
  threads/
    improve-thread-01/
      user-data/
        workspace/
        uploads/
        outputs/
      acp-workspace/

这些目录用于工具、沙箱、文件上传、输出产物。Agent 在工具里看到的虚拟路径通常是：
/mnt/user-data/workspace
/mnt/user-data/uploads
/mnt/user-data/outputs

## memory 和 session 不是一回事

memory:
  enabled: true
  storage_path: memory.json
  debounce_seconds: 30
  injection_enabled: true

MemoryMiddleware 会在每轮 agent 结束后，把用户输入和最终回答过滤出来，放进 memory queue。它会忽略工具中间过程，只保留有意义的用户消息和最终 AI 回复。

checkpoint = 当前会话状态，按 thread_id 恢复多轮上下文
memory.json = 长期用户画像/偏好/事实，跨会话注入
threads/<thread_id>/ = 该会话的文件工作区

因此每次运行 main.py，只要 SQLite checkpoint 还在，它都会继续同一个会话。想开新会话，最直接就是改成新的 thread_id，比如：

## 4——session 会话过期与清理

目前这个项目没有实现 checkpoints.db 的自动过期清理 / prune。

1. Summarization 只压缩上下文，不清理数据库
2. memory 会提炼长期记忆，但也不是清理 session
MemoryMiddleware 会把对话提炼进 .deer_flow/memory.json 或 agent 私有 memory。它是“长期记忆提炼”，不是删除会话历史。


