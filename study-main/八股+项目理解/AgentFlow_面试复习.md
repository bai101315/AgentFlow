# AgentFlow 面试复习总览

> 项目路径：`C:\Users\BAI\Desktop\project`  
> 定位：一个本地优先的 Agent Runtime，用来学习和实现可靠个人助手背后的工程能力：工具治理、长期记忆、会话恢复、历史检索、技能演化、观测与兜底。

---

## 1. 一句话介绍

AgentFlow 是一个基于 LangChain / LangGraph 的本地 Agent 运行时。它不是单纯调用大模型聊天，而是围绕真实 Agent 系统需要的横切能力做工程化封装，包括：

- 自定义 Agent：每个 Agent 有自己的 `SOUL.md`、`memory.json`、模型配置、工具权限和技能列表。
- 工具治理：本地文件工具、bash、web、MCP、subagent 等统一接入，并通过 tool group 和 `tool_search` 控制暴露范围。
- 状态持久化：用 LangGraph Checkpointer 保存 thread 级运行状态，支持重启后继续对话。
- 长期记忆：用 `memory.json` 保存长期用户画像、偏好、背景和高置信 facts。
- Session Search：用 SQLite FTS5 保存可搜索的历史消息，支持跨 session 检索。
- Observability：记录每轮 trace、model call、tool call、token、cache、错误和恢复情况。
- 兜底策略：模型配置 fallback、prompt cache 签名失效重建、Memory 写入阈值和去重、工具错误处理、循环检测、澄清拦截等。

面试可以先这样说：

```text
我的项目重点不是做一个 UI 很完整的聊天产品，而是实现一个本地 Agent runtime。核心是把 Agent 执行中容易混在一起的能力拆开治理：LangGraph 负责执行和 checkpoint，Memory 负责长期语义记忆，Session Search 负责历史检索，Tool Governance 负责工具暴露和权限，Observability 负责调试和评估。这样 Agent 主流程只做编排，各能力可以按配置启停和替换。
```

---

## 2. 核心目录

```text
project/
├── main.py                         # CLI 入口，创建/运行 Agent
├── config.yaml                     # 本地私有配置：模型、工具、memory、checkpoint 等
├── extensions_config.json          # MCP / 扩展配置
├── backend/
│   ├── agents/
│   │   ├── lead_agent/agent.py      # make_lead_agent，组装模型、工具、中间件
│   │   ├── lead_agent/prompt.py     # system prompt 构建
│   │   ├── checkpointer/            # SQLite / memory / postgres checkpointer
│   │   ├── memory/                  # memory.json 存储、队列、LLM 更新
│   │   ├── middlewares/             # prompt cache、memory、session search、loop detection 等
│   │   └── thread_state.py          # LangGraph state schema
│   ├── session_search/              # SQLite FTS5 历史检索
│   ├── observability/               # trace / tool call / model call 观测系统
│   ├── tools/                       # 工具加载、内置工具、tool_search
│   ├── sandbox/                     # 本地沙箱路径映射和安全校验
│   └── config/                      # 配置解析和路径管理
├── skills/                          # public/custom skills
└── .agentflow/                      # 本地运行状态，默认不提交
```

---

## 3. 默认运行状态目录

当前项目默认把运行状态放到：

```text
.agentflow/
├── agents/
│   └── {agent_name}/
│       ├── config.yaml
│       ├── SOUL.md
│       └── memory.json
├── threads/
│   └── {thread_id}/
│       └── user-data/
│           ├── workspace/
│           ├── uploads/
│           └── outputs/
├── checkpoints.db
├── session_search.db
├── observability.db
├── session_logs/
├── observability/
├── agent_threads.yaml
└── last_agent.txt
```

`AGENTFLOW_HOME` 可以覆盖这个位置，`DEER_FLOW_HOME` 是兼容旧版本的 fallback。

注意：你磁盘上可能同时有 `.agentflow` 和 `.deer_flow`。当前代码默认是 `.agentflow`，`.deer_flow` 更像历史遗留状态目录。

---

## 4. 核心概念：agent_name 和 thread_id

这个项目里最重要的两个 ID 是：

```text
agent_name = Agent 身份 / 人设 / 长期记忆 / 工具配置
thread_id  = 会话线程 / checkpoint / workspace / session search / observability 聚合单位
```

### agent_name 负责什么

每个自定义 Agent 有自己的目录：

```text
.agentflow/agents/{agent_name}/
├── config.yaml
├── SOUL.md
└── memory.json
```

它决定：

- 这个 Agent 的角色、人设和沟通风格。
- 这个 Agent 使用哪些工具组。
- 是否有模型 override。
- 长期记忆写到哪个 `memory.json`。
- 可用 skills 是哪些。

### thread_id 负责什么

每个 Agent 默认绑定一个稳定的 thread_id，映射存在：

```text
.agentflow/agent_threads.yaml
```

thread_id 决定：

- LangGraph checkpoint 存取。
- session search 的 thread 归属。
- observability 的 trace 聚合。
- 文件工作区路径。
- 用户上传和输出产物路径。

面试表达：

```text
agent_name 和 thread_id 是分开的。agent_name 表示当前使用哪个 Agent，它关联 SOUL、memory、模型和工具权限；thread_id 表示当前会话线程，它关联 checkpoint、workspace、session search 和 observability。这样不同 Agent 的人格和长期记忆不会混在一起，同时同一个 Agent 可以稳定恢复自己的会话上下文。
```

---

## 5. 一轮对话的完整执行流程

从 `main.py` 看，一轮用户输入大概是：

```text
1. 启动 CLI
2. 加载 config.yaml
3. 初始化 MCP tools
4. 创建 make_checkpointer()
5. 根据 active_agent / last_agent 找 agent_name
6. 根据 agent_name 找或创建 thread_id
7. 创建 ObservabilityRecorder
8. make_lead_agent(config, checkpointer, custom_middlewares=[ObservabilityMiddleware()])
9. 用户输入
10. observability.start_trace()
11. agent.ainvoke({"messages": [HumanMessage(user_input)]}, config, context={"thread_id": thread_id})
12. LangGraph 执行模型和工具循环
13. 中间件链在 before/after/wrap 阶段工作
14. checkpointer 按 thread_id 写入 checkpoints.db
15. MemoryMiddleware after_agent 入队长期记忆更新
16. SessionSearchMiddleware after_agent 索引历史消息
17. observability.end_trace()
18. 写 observability.db、thread summary、session_logs view
19. 打印最终回答
```

可以画成：

```text
User Input
  ↓
main.py
  ↓
Observability start_trace
  ↓
LangGraph Agent.ainvoke
  ↓
PromptCacheMiddleware 注入冻结 system prompt
  ↓
LLM 产生回复 / tool_calls
  ↓
工具执行 + 中间件治理
  ↓
LoopDetection / ToolError / Clarification 等兜底
  ↓
最终 AIMessage
  ↓
LangGraph Checkpointer 写 checkpoints.db
  ↓
MemoryMiddleware 入队更新 memory.json
  ↓
SessionSearchMiddleware 写 session_search.db
  ↓
Observability 写 observability.db
  ↓
User sees final answer
```

---

## 6. Agent 如何创建

入口是：

```python
make_lead_agent(config, checkpointer, custom_middlewares)
```

核心步骤：

```text
1. 从 config.configurable 读取运行参数
   - thinking_enabled
   - reasoning_effort
   - model_name
   - is_plan_mode
   - subagent_enabled
   - tools_enabled
   - max_concurrent_subagents
   - is_bootstrap
   - agent_name

2. 根据 agent_name 加载 agent_config
   - model override
   - api_key / base_url / provider_model
   - tool_groups
   - skills

3. 解析模型
   - request 指定优先
   - agent config 其次
   - global default 兜底
   - 默认偏向 deepseek-v4

4. 创建 PromptCacheMiddleware
   - 根据 model、agent、tool_groups、skills、subagent 等生成 signature
   - prompt_builder 指向 build_session_prompt()

5. 加载工具
   - get_available_tools(model_name, groups, subagent_enabled)
   - tool_groups 控制工具权限
   - MCP tools / builtins / subagent tools 合并

6. 组装中间件链
   - runtime middlewares
   - prompt cache
   - todo
   - memory
   - session search
   - background review
   - deferred tool filter
   - loop detection
   - observability
   - clarification

7. 调用 LangChain create_agent()
   - model
   - tools
   - middleware
   - system_prompt=PROMPT_CACHE_PLACEHOLDER
   - checkpointer
   - state_schema=ThreadState
```

为什么 system prompt 是 placeholder？

```text
因为真正的 system prompt 由 PromptCacheMiddleware 在模型调用前动态注入。这样可以把构建好的 prompt 冻结到 state 里，后续同一个 thread 重用，减少重复构建，也有利于 prompt cache 命中。
```

---

## 7. 中间件链

AgentFlow 的一个重点是把横切能力做成 middleware，不直接塞进主 Agent 逻辑。

主要中间件：

| Middleware | 作用 |
|---|---|
| ThreadDataMiddleware | 根据 thread_id 注入 workspace/uploads/outputs |
| SandboxMiddleware | 管理 sandbox_id 和沙箱生命周期 |
| ToolErrorHandlingMiddleware | 工具报错转成可被模型理解的 ToolMessage |
| DanglingToolCallMiddleware | 修复缺失 ToolMessage 的历史状态 |
| PromptCacheMiddleware | 冻结并注入 system prompt |
| TodoMiddleware | plan mode 下任务清单管理 |
| TitleMiddleware | 可选生成标题 |
| MemoryMiddleware | 对话结束后触发长期记忆更新 |
| SessionSearchMiddleware | 对话结束后索引到 SQLite FTS5 |
| BackgroundReviewMiddleware | 后台复盘并更新 skill |
| CuratorMiddleware | skill 生命周期维护 |
| DeferredToolFilterMiddleware | 隐藏 deferred tools，按需 promote |
| LoopDetectionMiddleware | 检测重复工具调用循环 |
| ObservabilityMiddleware | 记录 model/tool 调用 |
| ClarificationMiddleware | 澄清拦截，通常放最后 |

面试表达：

```text
我没有把 Memory、Session Search、Observability、Tool Error、Loop Detection 全写在主循环里，而是做成 middleware 链。这样主 Agent 只负责模型和工具编排，横切能力可以独立启停、测试和替换。
```

---

## 8. Prompt 构建和 Prompt Cache

### Prompt 包含哪些内容

`lead_agent/prompt.py` 里会构建一个完整 system prompt，大致包括：

```text
<role>                  Agent 身份
<soul>                  SOUL.md，人设和边界
<memory>                memory.json 注入的长期记忆
<thinking_style>        思考风格
<clarification_system>  澄清策略
<skill_system>          skills 列表和加载规则
<session_search_system> session_search 使用规则
<available-deferred-tools> 延迟工具列表
<subagent_system>       子 Agent 编排策略
<working_directory>     工作目录和输出规范
<response_style>        回复风格
<citations>             引用要求
<critical_reminders>    关键提醒
<current_date>          当前日期
```

### 为什么要冻结 prompt

普通做法是每轮重新拼 system prompt，但这会导致：

- memory、skills、tools、日期等轻微变化影响 prompt cache。
- 重启后 prompt 不一致，cache miss。
- 每轮构造成本变高。

AgentFlow 的策略：

```text
首次进入 thread 时构建完整 prompt
  ↓
保存到 state["cached_system_prompt"]
  ↓
同时保存 signature
  ↓
后续模型调用直接复用冻结 prompt
  ↓
如果模型/agent/tool_groups/skills/subagent 等变了，signature 变化，自动重建
```

`PromptCacheMiddleware` 的关键逻辑：

```python
if cached_prompt and cached_signature == self._signature:
    return cached_prompt, {}

prompt = self._prompt_builder()
updates = {
    "cached_system_prompt": prompt,
    "cached_system_prompt_signature": self._signature,
}
```

面试表达：

```text
Prompt Cache 的核心是 byte-stable。项目不是每轮都重新拼 prompt，而是把 prompt 冻结到 LangGraph state 里，后续同一 thread 直接复用。为了避免配置变化导致旧 prompt 误用，我会基于 model、agent_name、tool_groups、skills、subagent 开关等计算 signature。signature 一变就重建 prompt。
```

---

## 9. Checkpoint：为什么有 checkpoints 和 writes 两张表

配置：

```yaml
checkpointer:
  type: sqlite
  connection_string: checkpoints.db
```

实际数据库：

```text
.agentflow/checkpoints.db
```

表结构：

```text
checkpoints  保存完整状态快照
writes       保存 checkpoint 过程中的增量 channel 写入
```

### checkpoints 表

字段：

```text
thread_id
checkpoint_ns
checkpoint_id
parent_checkpoint_id
type
checkpoint BLOB
metadata BLOB
```

作用：

```text
保存某一时刻完整 graph state，用于同一个 thread_id 下恢复上下文。
```

里面可能包含：

```text
messages
HumanMessage / AIMessage / ToolMessage
tool_calls
tool results
thread_data
todos
artifacts
uploaded_files
cached_system_prompt
cached_system_prompt_signature
LangGraph 内部 channel_versions / versions_seen
```

### writes 表

字段：

```text
thread_id
checkpoint_ns
checkpoint_id
task_id
idx
channel
type
value BLOB
```

作用：

```text
记录每个 checkpoint 形成过程中各个 task 对 channel 的增量写入。
```

例如：

```text
节点 A 写入 messages
节点 B 写入 tool result
节点 C 写入 artifacts
最后形成一个 checkpoint
```

`writes` 会比 `checkpoints` 多，因为一次完整快照可能对应多条增量写入。

### 为什么用 SQLite

当前项目是本地单机 Agent runtime，SQLite 的优势是：

- 零部署，一个文件即可。
- LangGraph 原生支持 `AsyncSqliteSaver`。
- 很适合按 `thread_id/checkpoint_id` 做本地状态恢复。
- checkpoint 中的大对象可以序列化成 BLOB。
- 对本地开发、demo、个人助手场景足够。

如果要多进程、多用户、高并发部署，可以切 Postgres。配置里已经预留：

```yaml
checkpointer:
  type: postgres
  connection_string: postgresql://...
```

面试表达：

```text
checkpoints 保存完整快照，用来恢复；writes 保存每一步的增量 channel 写入，用来保证 LangGraph 图执行过程可以正确合并和恢复。SQLite 是本地单机最合适的选择，零部署且 LangGraph 原生支持。如果变成多实例服务，我会切到 Postgres。
```

---

## 10. Memory 长期记忆

Memory 不保存完整聊天记录，而保存长期语义记忆。

实际路径：

```text
.agentflow/agents/{agent_name}/memory.json
```

结构：

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
  "facts": [
    {
      "id": "fact_xxx",
      "content": "...",
      "category": "preference",
      "confidence": 0.95,
      "createdAt": "...",
      "source": "thread_id"
    }
  ]
}
```

### Memory 更新流程

```text
一轮 Agent 执行结束
  ↓
MemoryMiddleware.after_agent()
  ↓
过滤 messages
  - 保留 HumanMessage
  - 保留最终 AIMessage
  - 跳过 ToolMessage
  - 跳过带 tool_calls 的中间 AIMessage
  - 清理 <uploaded_files>
  ↓
检测 correction / reinforcement 信号
  ↓
加入 MemoryUpdateQueue
  ↓
debounce 等待
  ↓
MemoryUpdater 读取当前 memory.json
  ↓
拼 MEMORY_UPDATE_PROMPT
  ↓
调用 LLM 生成更新指令 JSON
  ↓
后端 _apply_updates 合并
  ↓
过滤低 confidence facts
  ↓
去重、截断 max_facts
  ↓
原子写入 memory.json
```

### 为什么不用 Checkpoint 代替 Memory

Checkpoint 是完整运行状态，里面有大量临时信息：

- tool calls
- tool results
- 上传文件路径
- 中间消息
- LangGraph 内部状态

如果直接用 checkpoint 做长期记忆：

- token 成本高。
- 噪声多。
- 容易把临时文件路径、工具失败、一次性任务写进长期上下文。

Memory 则是沉淀后的长期事实。

面试表达：

```text
Checkpoint 解决的是“接着聊”，Memory 解决的是“长期记住”。前者保存完整 thread state，后者只保存经过筛选和 LLM 提炼后的用户画像、偏好、背景和高置信 facts。两者不能互相替代。
```

---

## 11. Memory 污染控制

Memory 最怕污染，所以项目做了多层控制：

### 1. 消息过滤

只把适合长期记忆的内容送给 updater：

```text
保留用户真实输入
保留最终 assistant 回复
跳过 ToolMessage
跳过中间 tool_calls
去掉 uploaded_files
```

### 2. Prompt schema 约束

LLM 不直接覆盖 `memory.json`，而是返回更新指令：

```json
{
  "user": {
    "workContext": { "summary": "...", "shouldUpdate": true },
    "personalContext": { "summary": "...", "shouldUpdate": false },
    "topOfMind": { "summary": "...", "shouldUpdate": true }
  },
  "history": {
    "recentMonths": { "summary": "...", "shouldUpdate": true }
  },
  "newFacts": [
    {
      "content": "...",
      "category": "preference",
      "confidence": 0.9
    }
  ],
  "factsToRemove": ["fact_id"]
}
```

### 3. 后端合并

代码只按固定字段合并，不信任 LLM 整体输出。

```text
shouldUpdate=true 才更新 summary
低于 fact_confidence_threshold 不存
重复 fact 跳过
超过 max_facts 按 confidence 截断
source/thread_id 自动补
createdAt/id 自动补
```

### 4. 原子写入

先写临时文件，再 replace：

```text
memory.tmp -> memory.json
```

避免写到一半程序崩溃导致 JSON 损坏。

面试表达：

```text
LLM 只负责语义提炼，代码负责格式校验、阈值过滤、去重、合并和落盘。这样可以降低记忆污染，而不是让 LLM 直接改整个 memory.json。
```

---

## 12. Session Search 历史检索

Session Search 不是 Memory，它保存“以前聊过什么”的可搜索副本。

数据库：

```text
.agentflow/session_search.db
```

核心表：

```text
session_messages
session_messages_fts
session_messages_fts_data
session_messages_fts_idx
...
```

主表字段：

```text
id
dedupe_key
session_id
thread_id
message_id
role
content
ts
ordinal
active
source
metadata
```

工作方式：

```text
SessionSearchMiddleware.after_agent()
  ↓
取 state["messages"]
  ↓
过滤可索引消息
  - user message
  - final assistant reply
  - 跳过 tool_calls
  - 清理 uploaded_files
  ↓
写 session_messages
  ↓
同步写 FTS5 虚拟表
```

支持能力：

- `query`：全文检索历史。
- `recent`：浏览最近 indexed sessions。
- `session_id`：读取某个 session 的头尾消息。
- `session_id + around_message_id`：查看某条命中附近上下文。
- `active=0`：软删除 / prune。

为什么用 SQLite FTS5：

- 本地无需 Elasticsearch。
- 支持全文检索和 snippet。
- 数据量不大，SQLite 足够。
- 和本地 Agent runtime 的部署模式匹配。

面试表达：

```text
Memory 记录长期应该继续成立的事实，Session Search 记录过去具体发生过什么。用户问“上次我们说了什么”时应该用 session_search，而不是用 memory 猜。
```

---

## 13. Observability 观测系统

Observability 是这个项目很重要的工程亮点。

数据库：

```text
.agentflow/observability.db
```

表：

```text
traces
spans
model_calls
tool_calls
cache_events
thread_totals
trajectory_exports
legacy_imports
```

### 每轮 trace 记录什么

一轮用户输入到最终回答，对应一个 trace：

```text
trace_id
thread_id
agent_name
model_name
started_at / ended_at
elapsed_ms
completed
failure_reason
content_mode
user_input_preview
assistant_output_preview
usage_json
summary_json
```

### model_calls

记录：

```text
model_name
input_tokens
output_tokens
total_tokens
billable_input_tokens
prompt_cache_hit_tokens
prompt_cache_miss_tokens
prompt_cache_hit_rate
elapsed_ms
preview_json
```

### tool_calls

记录：

```text
tool_name
status
error_type
elapsed_ms
args_preview / args_hash
result_preview / result_hash
retry_count
payload_json
```

### 为什么要 Observability

真实 Agent 很多问题最终答案看不出来，比如：

- 工具失败了一次，但后来重试成功。
- prompt cache 命中率很低。
- 某轮输入 token 突然爆炸。
- 工具调用耗时异常。
- 模型绕了很多圈才回答。

Observability 让这些内部现象可见。

### JSON 视图

SQLite 是 canonical store，但也会导出人类可读文件：

```text
.agentflow/observability/threads/{thread_id}.json
.agentflow/session_logs/{thread_id}.json
.agentflow/observability/traces/{date}/{thread_id}/{trace}.json
.agentflow/observability/trajectory_samples.jsonl
.agentflow/observability/failed_trajectories.jsonl
```

详细 trace 文件不是每轮都写，只在：

- trace failed
- `/obs full`
- slow trace
- high billable input tokens
- low cache hit rate

面试表达：

```text
Observability 不是简单打日志，而是把每轮 turn 结构化成 trace，并拆成 model_calls、tool_calls、cache_events 和 thread_totals。这样即使最终回答成功，也能看到中间是否有工具失败、是否重试恢复、token 和 cache 是否异常。
```

---

## 14. Tool Governance 工具治理

工具来源：

- 配置化工具：web、file read/write、bash。
- 内置工具：session_search、skill_manage、tool_search 等。
- MCP tools：从外部 MCP server 自动发现。
- subagent tool：任务分解时调用子 Agent。
- ACP tools：可配置外部 agent adapter。

### tool group

`config.yaml` 里通过 group 控制工具权限：

```yaml
tool_groups:
  - name: web
  - name: file:read
  - name: file:write
  - name: bash
```

每个自定义 Agent 也可以有自己的 tool_groups。

### tool_search / deferred tools

问题：如果 MCP tools 很多，一次性把所有 schema 暴露给模型会导致：

- prompt 变长。
- token 成本高。
- 工具选择干扰大。
- 模型容易误调用不相关工具。

方案：

```text
DeferredToolRegistry 保存完整工具列表
  ↓
系统 prompt 只列 deferred tool 名称
  ↓
模型需要时先调用 tool_search
  ↓
tool_search 根据 query 检索相关工具
  ↓
promote 后 DeferredToolFilterMiddleware 才允许调用
```

面试表达：

```text
MCP 工具不是越多越好。AgentFlow 通过 tool_search 做渐进式工具披露，初始只暴露一个轻量发现入口，具体工具按需 promote。这样减少 token，也减少模型在工具选择上的干扰。
```

---

## 15. MCP 在项目里的位置

MCP 不是“只用了一个函数”。项目主要落地的是 MCP Tools 接入：

```text
MCP Server 暴露工具
  ↓
项目启动时 initialize_mcp_tools()
  ↓
MCP client 连接 server
  ↓
tools/list 自动发现工具 schema
  ↓
注册到工具系统
  ↓
如果 tool_search 开启，则进入 deferred registry
  ↓
模型通过 tool_search 按需发现和调用
```

可以讲的点：

- MCP 是 Agent 接外部能力的标准协议，不是单个函数。
- MCP 有 Host / Client / Server 三种角色。
- 能力包括 Tools、Resources、Prompts。
- 通讯底层常见是 JSON-RPC 2.0。
- 传输可以是 stdio、SSE、Streamable HTTP。
- 项目主要落地 Tools，Resources/Prompts 可以作为协议层扩展点。
- 多 server 可统一接入，并由 tool governance 控制是否暴露。
- 和 Function Calling 区别：Function Calling 是模型调用函数的接口规范，MCP 是外部工具/资源的标准接入协议。

---

## 16. Sandbox 和文件隔离

项目通过 `ThreadDataMiddleware` 根据 thread_id 注入路径：

```text
workspace_path
uploads_path
outputs_path
```

工具里看到的虚拟路径：

```text
/mnt/user-data/workspace
/mnt/user-data/uploads
/mnt/user-data/outputs
```

本地路径映射到：

```text
.agentflow/threads/{thread_id}/user-data/...
```

当前项目里 `sandbox_work_dir()` 被改成暴露 project root，这对本地编码方便，但安全性要在面试里说清楚：

```text
本地模式不是强安全隔离，host bash 和 project root 暴露适合可信开发环境。生产或不可信任务应该切 Docker sandbox，并关闭 host bash 或限制权限。
```

兜底：

- 校验 thread_id，只允许安全字符。
- 虚拟路径必须以 `/mnt/user-data` 开头。
- 解析路径后检查是否还在允许根目录下。
- host bash 是 opt-in。

---

## 17. Skill 系统和自我演化

Skill 是可复用工作流文档，不是普通记忆。

结构：

```text
skills/
├── public/
└── custom/
```

Prompt 里只注入 skill 索引，具体文件按需读取，这叫 progressive loading。

写入路径：

```text
skill_manage
```

`skill_manage` 负责：

- 限制只能写 `skills/custom`。
- 校验 skill name。
- 校验 markdown / frontmatter。
- 做安全扫描。
- 记录历史。
- 刷新 skills prompt cache。

BackgroundReviewMiddleware 会在后台根据工具调用和用户纠正信号，自动判断是否需要创建或 patch skill。

面试表达：

```text
Memory 存用户事实，Skill 存可复用流程。比如用户偏好中文回答应该进 memory；而“这个项目如何跑测试、如何修某类错误”的流程应该沉淀成 skill。
```

---

## 18. Subagent 设计

Subagent 用于复杂任务分解：

```text
主 Agent = orchestrator
  ↓
分解任务
  ↓
并发调用多个 task subagent
  ↓
收集结果
  ↓
综合回答
```

Prompt 里有硬性并发限制：

```text
max_concurrent_subagents
```

如果子任务超过限制，需要分批执行。

适合：

- 多角度研究。
- 大代码库并行分析。
- 多文件排查。
- 信息源并行收集。

不适合：

- 单步任务。
- 严格顺序依赖任务。
- 需要用户澄清的任务。

面试表达：

```text
Subagent 不是为了包装每个小操作，而是为了并行分解复杂任务。项目通过 prompt 和配置限制每轮最多 task 数，避免模型一次性发起过多子任务造成资源失控。
```

---

## 19. 兜底策略总览

### 1. 模型 fallback

如果请求指定的模型不存在：

```text
requested model 不合法
  ↓
fallback 到默认模型 deepseek-v4 或第一个配置模型
```

如果 thinking 开启但模型不支持：

```text
自动关闭 thinking_enabled
```

### 2. Prompt cache 兜底

如果缓存 prompt 不存在或 signature 不一致：

```text
重新构建 prompt
更新 cached_system_prompt
更新 cached_system_prompt_signature
```

### 3. Memory 兜底

- memory 文件不存在：返回 empty memory。
- JSON 解析失败：返回 empty memory 并打 warning。
- LLM 返回不是 JSON：解析失败则跳过本次更新。
- fact 低置信度：不写入。
- 重复 fact：跳过。
- 写文件：临时文件 + 原子替换。

### 4. Session Search 兜底

- FTS 查询失败：回退为空结果。
- 中文查询 FTS 无结果：fallback 到 LIKE。
- delete/prune：软删除 active=0，不直接物理删除。

### 5. Tool Error 兜底

工具异常不直接炸主流程，而是转成 ToolMessage，让模型能继续解释或换方法。

### 6. Loop Detection 兜底

重复工具调用检测：

```text
工具名 + 稳定参数 -> hash
最近 20 次滑动窗口
同一 hash 出现 3 次 -> 注入 warning
同一 hash 出现 5 次 -> 清空 tool_calls，强制最终回答
```

### 7. Clarification 兜底

如果需求缺信息、存在多种解释、涉及风险操作，则先澄清，不直接行动。

### 8. Observability 兜底

即使最终成功，也记录中间失败：

```text
had_any_failure
failed_tool_call_count
recovered_after_failure
failed_tool_names
```

让开发者知道这轮是否经历了失败恢复。

---

## 20. 为什么有多个 SQLite，不统一一个库

当前有：

```text
checkpoints.db       LangGraph 状态恢复
session_search.db    历史消息全文搜索
observability.db     trace / tool / model / cache 观测
```

为什么拆开：

- 职责不同。
- schema 生命周期不同。
- 查询模式不同。
- 归属模块不同。
- checkpoint 是 LangGraph saver 管理，不适合混进业务表。
- session_search 需要 FTS5。
- observability 是调试数据，可以单独清理或迁移。

面试表达：

```text
我没有把所有数据塞进一个 SQLite，因为这三类数据的读写模式和归属完全不同。checkpoint 是 LangGraph 管理的运行状态；session_search 是 FTS5 检索索引；observability 是结构化 trace。拆开后模块边界更清晰，也方便单独清理和演进。
```

---

## 21. 数据保存形式总表

| 数据 | 保存形式 | 路径 | 用途 |
|---|---|---|---|
| LangGraph checkpoint | SQLite | `.agentflow/checkpoints.db` | 恢复 thread 精确状态 |
| Session Search | SQLite FTS5 | `.agentflow/session_search.db` | 跨 session 搜历史 |
| Observability | SQLite | `.agentflow/observability.db` | trace / tool / model / cache |
| Long-term Memory | JSON | `.agentflow/agents/{agent}/memory.json` | 用户画像、facts |
| Agent 人设 | Markdown | `.agentflow/agents/{agent}/SOUL.md` | 角色、风格、边界 |
| Agent 配置 | YAML | `.agentflow/agents/{agent}/config.yaml` | 模型、工具、skills |
| Agent-thread 映射 | YAML | `.agentflow/agent_threads.yaml` | agent_name -> thread_id |
| 当前 Agent | TXT | `.agentflow/last_agent.txt` | 下次启动恢复选择 |
| Session summary view | JSON | `.agentflow/session_logs/{thread_id}.json` | 人类可读摘要 |
| Thread summary | JSON | `.agentflow/observability/threads/{thread_id}.json` | 观测摘要 |
| Detailed trace | JSON | `.agentflow/observability/traces/...` | 异常/完整 trace |
| Trajectory | JSONL | `.agentflow/observability/*.jsonl` | eval / replay 数据 |
| 文件产物 | 普通目录 | `.agentflow/threads/{thread}/user-data` | uploads/outputs/workspace |

---

## 22. 常见面试追问

### Q1：你的项目和普通 Function Calling Agent 有什么区别？

```text
普通 Function Calling 主要解决“模型如何调用函数”。我的项目解决的是更完整的 Agent Runtime 问题，包括工具权限、工具发现、状态持久化、长期记忆、历史检索、prompt cache、观测、错误兜底和技能沉淀。Function Calling 是其中的工具调用接口，AgentFlow 更关注运行时治理。
```

### Q2：为什么 Memory 不直接存聊天记录？

```text
聊天记录有大量临时噪声，比如 tool calls、工具结果、上传文件路径和一次性任务。如果直接注入，token 成本高，也会污染长期上下文。所以 memory.json 只保存经过过滤和 LLM 提炼后的长期语义信息，包括用户画像、阶段摘要和高置信 facts。
```

### Q3：Session Search 和 Memory 区别？

```text
Session Search 回答“过去发生过什么”，Memory 回答“未来仍然应该记住什么”。Session Search 用 SQLite FTS5 保存可搜索的历史消息；Memory 用 JSON 保存长期事实和偏好。
```

### Q4：为什么用 checkpoint？

```text
因为 Agent 执行不只是简单聊天，而是有 messages、tool_calls、tool results、todos、artifacts、cached_system_prompt 等状态。Checkpoint 可以按 thread_id 保存完整 LangGraph state，进程重启后仍然能继续同一条会话。
```

### Q5：为什么 checkpoint 有 checkpoints 和 writes 两张表？

```text
checkpoints 是完整状态快照，用于恢复；writes 是每个 checkpoint 形成过程中的增量 channel 写入，用于 LangGraph 的执行恢复和状态合并。一次 checkpoint 可能包含多条 channel write，所以 writes 行数通常更多。
```

### Q6：为什么用 SQLite？

```text
当前项目是本地单机 Agent，SQLite 零部署、文件化、足够稳定，并且 LangGraph 原生支持 SQLite checkpointer。session_search 还能用 SQLite FTS5。高并发多实例部署时，checkpoint 可以切到 Postgres。
```

### Q7：Memory 如何防污染？

```text
先过滤消息，只保留用户输入和最终回答；然后让 LLM 返回更新指令 JSON，而不是直接覆盖 memory；后端再做 shouldUpdate、confidence 阈值、去重、max_facts 截断、上传文件清理和原子写入。LLM 负责语义判断，代码负责落盘约束。
```

### Q8：工具太多会怎样处理？

```text
通过 tool_search 做 deferred tool disclosure。初始只暴露工具搜索入口和 deferred tool 名称，不把所有 MCP schema 一次性塞给模型。模型需要时先搜索，再 promote 相关工具，减少 token 和误调用。
```

### Q9：Observability 有什么价值？

```text
Agent 的失败经常隐藏在中间过程里，最终回答可能成功但中间工具失败过。Observability 把每轮记录成 trace，并拆出 model_calls、tool_calls、cache_events 和 thread_totals，可以看到 token、cache、耗时、错误和是否 recovery。
```

### Q10：这个项目有哪些不足？

可以诚实说：

```text
目前主要是本地单机 runtime，还没有完整 Web UI；SQLite 适合本地，但多用户高并发要迁移 Postgres；memory 更新仍依赖 LLM 判断，虽然有阈值和去重，但不能完全消除污染；本地 sandbox 不是强隔离，生产需要 Docker sandbox 和更严格权限策略；evaluation harness 还可以继续完善。
```

---

## 23. 适合面试时重点展开的亮点

如果面试官让你讲项目，不要平均用力。建议优先讲这几个：

1. **Agent Runtime 分层**
   - Agent 主流程只做编排。
   - Memory、Tool、Sandbox、Observability、Session Search 都做成模块。

2. **Checkpoint vs Memory vs Session Search**
   - checkpoint：精确恢复状态。
   - memory：长期用户画像和事实。
   - session_search：历史检索。

3. **Prompt Cache**
   - 冻结 system prompt。
   - signature 控制失效。
   - 提高 prompt cache 命中。

4. **Tool Governance**
   - tool group 权限。
   - MCP tools 自动发现。
   - deferred tool_search 渐进披露。

5. **Observability**
   - trace/model/tool/cache 全链路记录。
   - 失败可见、恢复可见、成本可见。

6. **兜底策略**
   - Loop detection。
   - Clarification first。
   - Tool error handling。
   - Memory 防污染。

---

## 24. 两分钟项目介绍模板

```text
我做的 AgentFlow 是一个本地 Agent Runtime，重点不是 UI，而是 Agent 执行链路的工程治理。

底层用 LangChain/LangGraph 创建 Agent，通过 checkpointer 把每个 thread_id 的运行状态保存到 SQLite，保证重启后还能继续同一轮上下文。每个自定义 Agent 有独立的 SOUL.md、config.yaml 和 memory.json，所以不同 Agent 的人格、工具权限和长期记忆互相隔离。

工具层支持本地文件工具、bash、web、MCP tools 和 subagent。工具不是全部直接暴露给模型，而是通过 tool group 控制权限；MCP 工具较多时会进入 deferred registry，由 tool_search 按需检索和 promote，减少 token 和误调用。

记忆方面我把 checkpoint、memory 和 session search 分开。checkpoint 保存完整运行状态；memory.json 保存经过过滤和 LLM 提炼后的长期用户画像、偏好和 facts；session_search.db 用 SQLite FTS5 保存可搜索的历史消息，用来回答“之前聊过什么”。

可靠性上，我做了多层兜底：PromptCacheMiddleware 冻结 system prompt 并用 signature 控制失效；Memory 更新有 debounce、阈值、去重和原子写入；LoopDetection 防止重复工具调用；ToolErrorHandling 把工具异常转为模型可处理消息；ClarificationMiddleware 在需求不清时优先澄清。

最后还有 Observability，它把每轮对话记录成 trace，拆成 model call、tool call、cache event 和 thread totals，可以看到 token、prompt cache 命中率、工具失败和恢复情况，方便调试和后续评估。
```

---

## 25. 一句话收尾

```text
这个项目的核心价值是把一个“能聊天、能调工具”的 Agent，往“可恢复、可治理、可记忆、可检索、可观测、可演化”的 Agent Runtime 推进。
```

