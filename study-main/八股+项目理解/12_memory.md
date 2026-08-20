# 面试版本

```
“我的项目里有两套持久化机制。Checkpoint 是 LangGraph 的运行状态持久化，用 SQLite 保存每个 thread_id 的状态快照，包括 messages、tool calls、tool results、todos、title 等，它解决的是多轮对话恢复和进程重启后的续聊问题。
Memory 是长期语义记忆，用 memory.json 保存用户画像、历史摘要和高置信事实，它不是原始聊天记录，而是通过 MemoryMiddleware 在 agent 执行结束后过滤对话，再进入 debounce 队列，最后由 LLM 根据当前 memory 和新对话生成 JSON 更新指令，应用后原子写入文件。下一次创建 agent 时，系统会把 memory 格式化后注入 system prompt，从而实现个性化和长期上下文。”
```


# 整体框架

项目里有两套记忆相关机制：
```
1. Checkpoint / checkpointer
   保存 LangGraph 的完整线程状态，用来恢复同一个 thread_id 的多轮对话。

2. Memory / memory.json
   保存经过 LLM 提炼后的长期语义记忆，用来在未来对话里个性化提示词。
```

# checkpoint

## 为什么要配置sqlite？
checkpointer 是 LangGraph/LangChain agent **体系里的检查点机制**，但“机制”和“存储后端”是两件事。

```
checkpointer = 检查点接口/能力
sqlite = 检查点数据存在哪里
```

LangGraph 提供了 checkpointer 抽象，它知道什么时候保存 graph state、什么时候根据 thread_id 恢复 state。但是它不强制你必须存到哪里。你可以选择：
```
memory   -> 存进进程内存，重启就没了
sqlite   -> 存进本地 SQLite 文件，重启还能恢复
postgres -> 存进 PostgreSQL，适合多进程/服务化
```

当前是:
```yaml
checkpointer:
  type: sqlite
  connection_string: checkpoints.db
```

意思是：
```
启用 LangGraph 检查点机制，并把检查点落盘到 SQLite 文件 checkpoints.db
```

当前项目的流程是：
```text
1. 在 config.yaml 里配置使用哪种 checkpointer 后端
2. 根据配置创建对应的 LangGraph checkpointer 实例
3. 把这个实例传给 create_agent()
4. 后续由 LangGraph 按 thread_id 自动保存/恢复状态
```

项目真正做配置解析和实例创建的位置是：
```python
# backend/agents/checkpointer/async_provider.py
async with AsyncSqliteSaver.from_conn_string(conn_str) as saver:
    await saver.setup()
    yield saver

# 会有兜底逻辑，使用langchain内部的InMemorySaver
if config.checkpointer is None:
    from langgraph.checkpoint.memory import InMemorySaver

    yield InMemorySaver()

```
然后在创建agent时，传入进去；


checkpoint 里存的是 LangGraph 的 ThreadState 快照。你的状态结构在 thread_state.py (line 20)：

```python
class ThreadState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: NotRequired[list | None]
    uploaded_files: NotRequired[list[dict] | None]
```
AgentState 自带最重要的字段是：**messages**

所以它会存:
```
thread_id
checkpoint_id
parent_checkpoint_id
messages: HumanMessage / AIMessage / ToolMessage
tool_calls
tool outputs
title
todos
artifacts
uploaded_files
sandbox/thread_data
metadata
pending_writes
时间戳 ts
```

# memory.json
memory.json 是长期语义记忆，结构由 storage.py (line 18) 的 create_empty_memory() 定义：
```json
{
  "version": "1.0",
  "lastUpdated": "...",
  "user": {
    "workContext": { "summary": "", "updatedAt": "" },
    "personalContext": { "summary": "", "updatedAt": "" },
    "topOfMind": { "summary": "", "updatedAt": "" }
  },
  "history": {
    "recentMonths": { "summary": "", "updatedAt": "" },
    "earlierContext": { "summary": "", "updatedAt": "" },
    "longTermBackground": { "summary": "", "updatedAt": "" }
  },
  "facts": []
}
```
存储的是:
```
user.workContext        用户工作背景、项目、技术栈
user.personalContext    语言、偏好、个人兴趣
user.topOfMind          最近关注点、当前任务方向

history.recentMonths        最近几个月的重要互动
history.earlierContext      更早但仍有价值的背景
history.longTermBackground  长期稳定背景

facts                       结构化事实列表

facts每条大概是:
{
  "id": "fact_xxxxxxxx",
  "content": "用户偏好中文回答，并希望解释有清晰逻辑。",
  "category": "preference",
  "confidence": 0.95,
  "createdAt": "...",
  "source": "thread_id"
}
```

## 如何规范 LLM 输出标准 memory.json 格式？

流程：
```text
当前 memory.json
+ 本轮过滤后的对话
+ MEMORY_UPDATE_PROMPT 规则
  -> LLM 输出更新 JSON
  -> 后端解析 JSON
  -> _apply_updates() 合并到标准 memory 结构
  -> FileMemoryStorage.save() 保存 memory.json
```
靠三层约束：
### 第一层：Prompt 明确规定输出格式。
在 ```MEMORY_UPDATE_PROMPT```，里面明确要求
```
Return ONLY valid JSON, no explanation or markdown.
并且给了固定的schema
```
### 代码只接受 JSON。
在 updater.py (line 92) 里 _extract_json_payload() 会从模型输出中解析 JSON：

```python
payload = json.loads(cleaned)
if isinstance(payload, dict):
    return payload

# 如果模型输出了 markdown、解释、<think>，代码会尽量清洗：
_strip_think_blocks()
去掉 ```json 代码块
从文本中寻找第一个 JSON object

# 如果还是解析失败：会返回False
```

### 后端统一写入标准 memory 结构。
真正写入 memory 的地方是 updater.py (line 306) 的 _apply_updates()。
它不是把 LLM 输出整个覆盖进 memory.json，而是逐字段取：

```python
user_updates = update_data.get("user", {})
for section in ["workContext", "personalContext", "topOfMind"]:
    section_data = user_updates.get(section, {})
    if section_data.get("shouldUpdate") and section_data.get("summary"):
        current_memory["user"][section] = {
            "summary": section_data["summary"],
            "updatedAt": now,
        }
```
history、facts 也类似，还会有一些质量约束：
- 重复fact会跳过
- fact会有打分，得分角度的会抛弃
- 超过数量上限会排序并截断
  
最后保存由 storage.py (line 135) 完成：
```python
memory_data["lastUpdated"] = utc_now_iso_z()
json.dump(memory_data, f, indent=2, ensure_ascii=False)
temp_path.replace(file_path)
```

# 记忆更新——比较复杂

## 更新时机：
```
一次 User 输入
-> Agent 生成最终 AI 回复
-> MemoryMiddleware.after_agent() 触发
-> 把这一轮对话加入 MemoryUpdateQueue
-> 等 debounce_seconds
-> 调 LLM 更新 memory.json
```


## 1. Agent 结束后触发 MemoryMiddleware
入口在 memory_middleware.py (line 205)。**after_agent**
它是在一次 agent 执行结束后触发，不是在对话中间触发。设计理念是：**只在一轮完整交互结束后，才判断这轮有没有值得沉淀的长期记忆。**

先拿到
```python
thread_id = runtime.context.get("thread_id")
messages = state.get("messages", [])
```

## 过滤消息：只保留适合长期记忆的内容
```
filtered_messages = _filter_messages_for_memory(messages)

只会保留：
HumanMessage
最终 AIMessage

会丢弃：
ToolMessage
带 tool_calls 的中间 AIMessage
<uploaded_files> 上传文件块
纯上传文件消息
```

## 检测纠错/正反馈信号

过滤后会检测：
```
correction_detected = detect_correction(filtered_messages)
reinforcement_detected = not correction_detected and detect_reinforcement(filtered_messages)

```
设计理念：**用户纠正和用户确认都很有记忆价值**。纠错可以沉淀成“以后不要再这样做”，正反馈可以沉淀成“用户喜欢这种方式”。

实现方式：正则化表达式，显式查询

## 加入 MemoryUpdateQueue，而不是立刻更新

1. 会有一个更新队列，存```ConversationContext```,会找到所有的```self._queue = [c for c in self._queue if c.thread_id != thread_id]```,保留其他的thread_id，把新建的context加入队列末尾；

```python
queue.add(
    thread_id=thread_id,
    messages=filtered_messages,
    agent_name=self._agent_name,
    correction_detected=correction_detected,
    reinforcement_detected=reinforcement_detected,
)

# 队列设计
class MemoryUpdateQueue:
    self._queue = []
    self._lock = threading.Lock()
    self._timer = None
    self._processing = False

```

2. 会重建防抖计时器
```python
# 防抖的核心：只要新消息持续到来，上一次设定的处理就被作废，从而将多个更新合并到同一个批次中。
if self._timer is not None:
    self._timer.cancel()

# 创建一个新的 threading.Timer 对象，它会在 config.debounce_seconds 秒后在新线程中执行 self._process_queue。
self._timer = threading.Timer(
    config.debounce_seconds,
    self._process_queue,
)

# 当主程序退出时，Python 会等待所有非守护线程结束。如果定时器仍在倒计时，非守护线程会阻止进程立即退出。设为守护线程后，即使它还没有触发，主程序退出时也会自动被终止，不会导致进程挂起。
self._timer.daemon = True

```

## debounce 时间到，队列开始处理

核心代码：
```python
def _process_queue(self):
    updater = MemoryUpdater()

    for context in contexts_to_process:
        updater.update_memory(
            messages=context.messages,
            thread_id=context.thread_id,
            agent_name=context.agent_name,
            correction_detected=context.correction_detected,
            reinforcement_detected=context.reinforcement_detected,
        )
```

设计点：
```
self._processing: 一次只处理一个队列，否则就等待
contexts_to_process = self._queue.copy()：
开始执行这一批，把当前队列中的内容复制出来，清空队列，
这样在处理的过程中如果有新的加入，就会进入新的批次，不会干扰当前批次的处理
多个 context 之间 sleep 0.5s，避免限流
```

## MemoryUpdater 调 LLM 生成“更新指令”

```python
# 读取当前 memory：
current_memory = get_memory_data(agent_name)
# 把对话格式化
conversation_text = format_conversation_for_update(messages)
只保存用户User和Assistant内容

# 
if correction_detected or reinforcement_detected:
  加入字符串提示：存在明确纠正信号，特别注意

# 当前记忆+对话内容+提示语
prompt = MEMORY_UPDATE_PROMPT.format(
    current_memory=json.dumps(current_memory, indent=2),
    conversation=conversation_text,
    correction_hint=correction_hint,
)

```

## 解析 LLM 输出，不合法就跳过

```python
# LLM 输出后，代码会提取文本：
response_text = _extract_text(response.content).strip()
# 然后解析 JSON, 第二层规范方案
update_data = _extract_json_payload(response_text)

去掉 <think>...</think>
去掉 ```json 代码块
尝试 json.loads
如果前面有废话，就从第一个 { 开始尝试解析

```

## 后端合并更新，保证 memory.json 标准格式

真正修改 memory 的地方是 updater.py (line 306)：
```python
updated_memory = self._apply_updates(current_memory, update_data, thread_id)
```
它按固定字段更新，而不是让 LLM 覆盖整个文件。

这里就存在几层保护
```
confidence 低于阈值不存
重复 content 不存
超过 max_facts 后按 confidence 保留最高的
correction fact 可以带 sourceError
```

## 保存前再清理上传文件记忆

保存前还会执行：```updated_memory = _strip_upload_mentions_from_memory(updated_memory)```

会删除一下内容：
```
用户上传了文件
/mnt/user-data/uploads/...
<uploaded_files>
```

## 原子写入 memory.json

设计理念是：**先写临时文件，再原子替换正式文件**。这样就算程序中途崩溃，也不会留下写了一半的坏 JSON。
```
temp_path = file_path.with_suffix(".tmp")
json.dump(memory_data, f, indent=2, ensure_ascii=False)
temp_path.replace(file_path)
```
