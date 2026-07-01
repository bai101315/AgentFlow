# Hermes Agent Prompt Caching 详细架构参考

## 目录

1. [为什么需要 Prompt Caching](#1-为什么需要-prompt-caching)
2. [核心约束：前缀字节相等](#2-核心约束前缀字节相等)
3. [整体架构概览](#3-整体架构概览)
4. [机制一：三层 Prompt 构造](#4-机制一三层-prompt-构造)
5. [机制二：Frozen Snapshot（内存冻结快照）](#5-机制二frozen-snapshot内存冻结快照)
6. [机制三：时间戳精度降级](#6-机制三时间戳精度降级)
7. [机制四：SQLite 跨进程持久化](#7-机制四sqlite-跨进程持久化)
8. [机制五：Anthropic 显式 cache_control 标记](#8-机制五anthropic-显式-cache_control-标记)
9. [完整执行流程](#9-完整执行流程)
10. [Cache 失效条件](#10-cache-失效条件)
11. [不同 Provider 的缓存策略](#11-不同-provider-的缓存策略)
12. [实际效果数据](#12-实际效果数据)
13. [关键源码位置](#13-关键源码位置)

---

## 1. 为什么需要 Prompt Caching

Hermes Agent 的 system prompt 通常有 **15K-35K tokens**。如果没有缓存，每个对话轮次都要把这些 prompt 全部重新计算一遍：

- **成本**：system prompt 占单次输入 token 的 ~75%
- **延迟**：每次需要 3-5 秒额外计算时间

**有了缓存后**：system prompt 的 KV-cache 被复用，每个新轮次只计算增量部分（用户消息），成本降低 ~80%，延迟降低 ~80%。

---

## 2. 核心约束：前缀字节相等

LLM 提供商（Anthropic、DeepSeek、OpenAI）的 KV-cache 是基于**前缀字节完全相等**来命中的：

```
请求 N:   [system prompt] + [msg1] + [msg2] + [msg3]    → cache miss (新)
请求 N+1: [system prompt] + [msg1] + [msg2] + [msg3] + [msg4]  → cache HIT
请求 N+2: [different system prompt] + [msg1] + ...       → cache MISS (前缀变了)
```

**只要前缀中有一个字节不同，整个缓存就失效。** 这就是 Hermes 的 prompt caching 架构要解决的核心问题：如何让 system prompt 在整个 session 的所有 turn 中保持字节级稳定。

---

## 3. 整体架构概览

Hermes 用了**五个机制**协同工作来保证前缀稳定性：

```
┌──────────────────────────────────────────────────────────────┐
│                    AIAgent 初始化                             │
│  agent_init.py:406-408                                       │
│  调用 _anthropic_prompt_cache_policy()                        │
│  → 决定是否启用缓存 + 使用哪种布局                             │
│  → 设置 agent._use_prompt_caching                            │
│  → 设置 agent._cache_ttl ("5m" 或 "1h")                      │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│            每个 turn：run_conversation()                       │
│  conversation_loop.py:417-418                                 │
│                                                              │
│  if agent._cached_system_prompt is None:                     │
│      → _restore_or_build_system_prompt()                     │
│          ├── 从 SQLite 恢复 (命中 → 字节完全一致的复用)         │
│          └── 从头构建 (miss → 三层拼装 → 写入 SQLite)          │
│                                                              │
│  active_system_prompt = agent._cached_system_prompt           │
│                                                              │
│  每次 API 调用：                                              │
│  ├── 组装 api_messages = [system] + history + [new_user_msg]  │
│  ├── if _use_prompt_caching:                                 │
│  │      apply_anthropic_cache_control(messages)               │
│  │      → 注入 cache_control 断点标记                         │
│  └── 发送到 LLM API                                           │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 机制一：三层 Prompt 构造

**源码**：`agent/system_prompt.py::build_system_prompt_parts()` (行 60-284)

System prompt 被分成三个层级，按「稳定性递减」排列：

### STABLE — 从不变化（最大化前缀缓存）
- `SOUL.md` 或 `DEFAULT_AGENT_IDENTITY`（Agent 身份定义）
- 工具使用指引（`MEMORY_GUIDANCE`、`SKILLS_GUIDANCE`、`SESSION_SEARCH_GUIDANCE`）
- Skills 索引
- 环境提示（WSL、Termux 等）
- 平台提示（CLI vs Telegram 等）
- 工具使用强制执行指引（`TOOL_USE_ENFORCEMENT_GUIDANCE`）

> 构建逻辑：`system_prompt.py:84-219`。这些内容在 agent 实例生命周期内完全不变。

### CONTEXT — session 间可能变化，session 内不变
- 调用方传入的 `system_message`
- 项目根目录的 `AGENTS.md` / `.cursorrules` 等上下文文件

> 构建逻辑：`system_prompt.py:221-238`。每个 session 开始后内容稳定。

### VOLATILE — 每次 session 都可能变，但设计成字节稳定
- Memory 冻结快照（不是实时状态！）
- USER.md 冻结快照
- 时间戳（仅有日期，没有分钟）

> 构建逻辑：`system_prompt.py:240-278`

最终的 system prompt 是三个 layer 用 `\n\n` 拼接：
```python
# system_prompt.py:301-303
parts = build_system_prompt_parts(agent, system_message=system_message)
return "\n\n".join(p for p in (parts["stable"], parts["context"], parts["volatile"]) if p)
```

**关键设计决策**：整个 system prompt 作为一个字符串构建一次，缓存到 `agent._cached_system_prompt`，所有 turn 复用。**绝不**在 session 中间重新构建或重新注入任何部分——只有这样上游的 prompt cache 才能保持 warm。

---

## 5. 机制二：Frozen Snapshot（内存冻结快照）

**源码**：`tools/memory_tool.py::MemoryStore` (行 107-412)

### 数据结构：两份状态

```python
class MemoryStore:
    memory_entries: List[str]      # 实时状态，由 memory 工具调用改变
    user_entries: List[str]        # 实时状态，由 memory 工具调用改变
    _system_prompt_snapshot: Dict  # 冻结快照，在 load_from_disk() 时拍摄
```

### 工作流程

```
session 开始
  → load_from_disk()
    ├── 从 MEMORY.md / USER.md 读取 entries（实时状态）
    ├── 去重
    └── 拍摄 _system_prompt_snapshot（冻结快照）

session 中途
  → memory 工具调用（add/replace/remove）
    ├── 修改 memory_entries / user_entries（实时状态）
    ├── 调用 save_to_disk() 立即持久化到磁盘
    └── _system_prompt_snapshot 保持不变！（不更新）

system prompt 注入
  → format_for_system_prompt(target)
    └── return self._system_prompt_snapshot.get(target)  ← 冻结快照！
        不是实时状态！中段写入不改变 system prompt！

下一个 session
  → load_from_disk() 重新读取
    └── 看到上一次 session 写入的更新后的 MEMORY.md
    └── 拍摄新的 _system_prompt_snapshot
```

### 核心代码

```python
# memory_tool.py:361-372
def format_for_system_prompt(self, target: str) -> Optional[str]:
    """
    Return the frozen snapshot for system prompt injection.

    This returns the state captured at load_from_disk() time, NOT the live
    state. Mid-session writes do not affect this. This keeps the system
    prompt stable across all turns, preserving the prefix cache.
    """
    block = self._system_prompt_snapshot.get(target, "")
    return block if block else None
```

---

## 6. 机制三：时间戳精度降级

**源码**：`agent/system_prompt.py:264-271`

```python
from hermes_time import now as _hermes_now
now = _hermes_now()
# Date-only (not minute-precision) so the system prompt is byte-stable
# for the full day.  Minute-precision changes invalidate prefix-cache KV
# on every rebuild path (compression boundary, fresh-agent gateway turns,
# session resume without a stored prompt).
timestamp_line = f"Conversation started: {now.strftime('%A, %B %d, %Y')}"
# "Friday, May 29, 2026" — 仅日期，一天内字节稳定
# NOT "Friday, May 29, 2026 02:25:31 PM" — 每秒变化会破坏缓存
```

如果时间戳精确到分钟/秒，gateway 每来一条新消息都会生成新的时间戳，prefix cache 100% miss。

---

## 7. 机制四：SQLite 跨进程持久化

**源码**：`agent/conversation_loop.py::_restore_or_build_system_prompt()` (行 85-184)

### 问题

Gateway 模式下，每次收到新消息都会创建一个**全新的 AIAgent 实例**。如果没有持久化，每个新进程都会重新构建 system prompt，可能导致字节级差异（时间戳变化等），cache 全部 miss。

### 解决方案

将完整 system prompt 字符串存入 SQLite 的 sessions 表。

### 入口流程

```python
# conversation_loop.py:85-184
def _restore_or_build_system_prompt(agent, system_message, conversation_history):
    stored_prompt = None

    # 1. 尝试从 SQLite 恢复
    if conversation_history and agent._session_db:
        session_row = agent._session_db.get_session(agent.session_id)
        if session_row is not None:
            raw_prompt = session_row.get("system_prompt")
            if raw_prompt and raw_prompt != "":   # 有有效内容
                stored_prompt = raw_prompt         # → 直接复用！

    if stored_prompt:
        # 恢复路径：字节完全一致 → prefix cache 命中
        agent._cached_system_prompt = stored_prompt
        return

    # 2. 首次构建：从头拼装
    agent._cached_system_prompt = agent._build_system_prompt(system_message)

    # 3. 立即持久化到 SQLite，供后续 turn 复用
    if agent._session_db:
        agent._session_db.update_system_prompt(
            agent.session_id, agent._cached_system_prompt
        )
```

### SQLite 表结构

```sql
-- hermes_state.py:196
CREATE TABLE sessions (
    ...
    system_prompt TEXT,    -- 完整 system prompt 字符串
    ...
);
```

```python
# hermes_state.py:744-751
def update_system_prompt(self, session_id: str, system_prompt: str) -> None:
    def _do(conn):
        conn.execute(
            "UPDATE sessions SET system_prompt = ? WHERE id = ?",
            (system_prompt, session_id),
        )
    self._execute_write(_do)
```

### 四种存储状态

| 状态      | 含义                                | 行为                       |
| --------- | ----------------------------------- | -------------------------- |
| `missing` | session 行不存在                    | 正常的新 session，从头构建 |
| `null`    | 行存在但 `system_prompt` 为 NULL    | 遗留 session，警告后重建   |
| `empty`   | 行存在但 `system_prompt` 为空字符串 | 持久化 bug，警告后重建     |
| `present` | 行存在且有有效 prompt               | 直接复用，cache 命中       |

### CLI resume 也受益

`hermes --continue` 和 `/resume` 命令也会从 SQLite 恢复 cached system prompt，确保恢复后的 session 依然 cache 命中。

---

## 8. 机制五：Anthropic 显式 cache_control 标记

**源码**：`agent/prompt_caching.py` (全文 79 行)

### 策略：`system_and_3`

总共最多 4 个 breakpoint（Anthropic 的硬性限制）：

```
Breakpoint 1: System prompt      — "前面 15K tokens 请缓存"
Breakpoint 2: 倒数第 3 条非 system 消息
Breakpoint 3: 倒数第 2 条非 system 消息
Breakpoint 4: 倒数第 1 条非 system 消息
```

### 两种布局模式

#### 1. Native Anthropic 布局（`native_anthropic=True`）

直接用在 Anthropic 原生 API 上。`cache_control` 放在 content block 内部：

```json
{
  "role": "system",
  "content": [
    {
      "type": "text",
      "text": "You are an AI assistant...",
      "cache_control": {"type": "ephemeral"}
    }
  ]
}
```

#### 2. OpenRouter / 第三方 布局（`native_anthropic=False`）

用在 OpenRouter 或其他兼容 OpenAI-wire 的代理上。`cache_control` 放在 message 顶层：

```json
{
  "role": "system",
  "content": "You are an AI assistant...",
  "cache_control": {"type": "ephemeral"}
}
```

### 触发决策

```python
# agent/agent_runtime_helpers.py:1086-1188
def anthropic_prompt_cache_policy(agent, *, provider, base_url, api_mode, model):
    # Claude + 原生 Anthropic API → (True, True)   # 启用 + native 布局
    # Claude + OpenRouter / Nous Portal → (True, False)  # 启用 + envelope 布局
    # Claude + 第三方 Anthropic-compatible gateway → (True, True)
    # MiniMax + Anthropic endpoint → (True, True)
    # Qwen + Alibaba/OpenCode → (True, False)
    # 其他 → (False, False)  # 不启用
```

### TTL 配置

```yaml
# config.yaml (prompt_caching.cache_ttl)
prompt_caching:
  cache_ttl: "5m"    # 5 分钟（默认）或 "1h"（1 小时）
```

- **5m TTL**：写入 1.25× 定价，适合短会话
- **1h TTL**：写入 2× 定价，适合长会话（turn 间隔 > 5 分钟）

### 每个 API 调用时注入

```python
# conversation_loop.py:821-826
if agent._use_prompt_caching:
    api_messages = apply_anthropic_cache_control(
        api_messages,
        cache_ttl=agent._cache_ttl,
        native_anthropic=agent._use_native_cache_layout,
    )
```

---

## 9. 完整执行流程

以 **Gateway 收到一条 Telegram 消息** 为例：

```
1. Gateway 收到消息
   └── 根据 session_id 从 SQLite 恢复 conversation_history

2. 创建 AIAgent 实例
   └── agent_init.py:400-413
       ├── 调用 _anthropic_prompt_cache_policy()
       │   → 根据 provider/model/api_mode 决定是否启用缓存
       └── 设置 _use_prompt_caching, _use_native_cache_layout, _cache_ttl

3. run_conversation() 被调用
   │
   ├── conversation_loop.py:417-418
   │   if agent._cached_system_prompt is None:
   │       _restore_or_build_system_prompt()
   │       │
   │       ├── 尝试从 SQLite 读取 system_prompt
   │       │   └── 有 → 字节完全一致的复用 → cache 命中
   │       │
   │       └── 没有 → 调用 build_system_prompt_parts()
   │           ├── STABLE layer: SOUL.md + 工具指引 + 环境提示...
   │           ├── CONTEXT layer: context files...
   │           └── VOLATILE layer:
   │               ├── memory_tool.py::format_for_system_prompt("memory")
   │               │   → 返回冻结快照（不是实时状态！）
   │               ├── format_for_system_prompt("user")
   │               │   → 返回冻结快照
   │               └── 日期级时间戳 "Friday, May 29, 2026"
   │           → 写入 SQLite 供后续 turn 使用
   │
   ├── active_system_prompt = agent._cached_system_prompt  # 后续所有 turn 复用

4. 进入 API 调用循环
   │
   ├── 组装：
   │   api_messages = [{"role": "system", "content": active_system_prompt}]
   │                + conversation_history
   │                + [{"role": "user", "content": "用户新消息"}]
   │
   ├── if _use_prompt_caching:
   │       apply_anthropic_cache_control(api_messages)
   │       → system prompt + 最后 3 条非 system 消息 → 注入 cache_control
   │
   └── 发送到 LLM API
       ├── Anthropic 原生：content block 内 cache_control
       ├── OpenRouter：message 顶层 cache_control
       └── DeepSeek/OpenAI：自动前缀匹配（无需显式标记）
```

---

## 10. Cache 失效条件

Cache 只在以下情况**合法**失效：

### 唯一合法的 cache breaker：Context Compression

```python
# agent/conversation_compression.py:358-360
agent._invalidate_system_prompt()  # 清空 _cached_system_prompt
new_system_prompt = agent._build_system_prompt(system_message)
agent._cached_system_prompt = new_system_prompt
```

触发条件：对话历史接近模型上下文窗口上限。

`invalidate_system_prompt()` 还做了一件事：

```python
# agent/system_prompt.py:306-314
def invalidate_system_prompt(agent):
    agent._cached_system_prompt = None
    if agent._memory_store:
        agent._memory_store.load_from_disk()  # 重新从磁盘加载 memory
```

**代价**：compression 后的第一个 turn 需要全量重新计算 system prompt，cache miss。但这是必要代价——不压缩会触发 context overflow 错误。

### 可能导致 Cache Miss 的情况

| 情况                                    | 原因                         | 影响                                   |
| --------------------------------------- | ---------------------------- | -------------------------------------- |
| Context compression                     | 合法的 cache 重建            | 1 个 turn 的 miss（必要）              |
| 用户 `/new` 或 `/reset`                 | 新 session，新 system prompt | 正常，新 session 不需要复用旧 cache    |
| Gateway 进程重启 + 无 persisted session | SQLite 中无数据 → 从头构建   | 可能引入字节差异（时间戳变了）         |
| SQLite write 失败                       | 后续 turn 无法 restore       | 每个 turn 都 rebuild → 100% cache miss |
| 跨天                                    | 日期变化导致时间戳变化       | 仅新建 session 时受影响                |

---

## 11. 不同 Provider 的缓存策略

| Provider                                 | 缓存机制                                 | Cache-hit 定价 | Hermes 动作               |
| ---------------------------------------- | ---------------------------------------- | -------------- | ------------------------- |
| **Anthropic 原生**                       | 显式 `cache_control`（content block 内） | 满价的 10%     | 注入 `cache_control` 断点 |
| **Claude + OpenRouter**                  | 显式 `cache_control`（message 顶层）     | 满价的 10%     | 注入 `cache_control` 断点 |
| **Claude + 第三方 Anthropic-compatible** | 显式 `cache_control`                     | 10%（若实现）  | 注入 `cache_control` 断点 |
| **MiniMax + Anthropic endpoint**         | 显式 `cache_control`                     | 10%（0.1×）    | 注入 `cache_control` 断点 |
| **Qwen + Alibaba/OpenCode**              | 显式 `cache_control`（OpenAI-wire）      | 低于满价       | 注入 `cache_control` 断点 |
| **DeepSeek**                             | 自动前缀匹配                             | 满价的 50%     | 无需显式标记              |
| **OpenAI**                               | 自动前缀匹配                             | 满价的 50%     | 无需显式标记              |
| **OpenRouter**                           | 透明代理                                 | 透传上游       | 取决于上游 provider       |
| **其他**                                 | 不启用                                   | 全额           | Hermes 不发标记           |

---

## 12. 实际效果数据

以一个 **10-turn session，system prompt 15K tokens** 为例：

| Turn | 发送 tokens | Cache 命中 | 新计算量 | 成本 vs 全额 |
| ---- | ----------- | ---------- | -------- | ------------ |
| 1    | 15,050      | 0          | 15,050   | 100%         |
| 2    | 15,900      | 15,050     | 850      | ~20%         |
| 5    | 16,800      | 15,900     | 900      | ~20%         |
| 10   | 18,200      | 17,300     | 900      | ~20%         |

**结论**：第 1 个 turn 后，成本降低约 **80%**，延迟降低约 **80%**。

### 成本对比（以 Claude Sonnet 4 为例）

|                          | 无缓存    | 有缓存   |
| ------------------------ | --------- | -------- |
| 10-turn session 输入成本 | ~$0.15    | ~$0.04   |
| 节省                     | —         | **~73%** |
| 延迟/响应                | 3-5s 额外 | <1s 额外 |

---

## 13. 关键源码位置

| 文件                                        | 关键函数/区域                       | 作用                                  |
| ------------------------------------------- | ----------------------------------- | ------------------------------------- |
| `agent/system_prompt.py:60-284`             | `build_system_prompt_parts()`       | 三层 prompt 组装                      |
| `agent/system_prompt.py:287-303`            | `build_system_prompt()`             | 拼接 + 缓存到 `_cached_system_prompt` |
| `agent/system_prompt.py:306-314`            | `invalidate_system_prompt()`        | 清缓存 + 重载 memory                  |
| `agent/conversation_loop.py:85-184`         | `_restore_or_build_system_prompt()` | SQLite 恢复/构建/持久化               |
| `agent/conversation_loop.py:405-418`        | 入口判断                            | `if _cached_system_prompt is None`    |
| `agent/conversation_loop.py:790-826`        | API 调用前组装                      | 注入 `cache_control` 标记             |
| `agent/prompt_caching.py:1-79`              | `apply_anthropic_cache_control()`   | system_and_3 策略                     |
| `tools/memory_tool.py:361-372`              | `format_for_system_prompt()`        | 返回冻结快照                          |
| `tools/memory_tool.py:126-142`              | `load_from_disk()`                  | 拍摄快照                              |
| `agent/agent_runtime_helpers.py:1086-1188`  | `anthropic_prompt_cache_policy()`   | 决定是否启用 + 用哪种布局             |
| `agent/agent_init.py:400-413`               | agent 初始化                        | 调用 policy + 设置 TTL                |
| `agent/conversation_compression.py:358-360` | compression 触发                    | invalidate + rebuild                  |
| `hermes_state.py:196`                       | `sessions` 表定义                   | 包含 `system_prompt` 列               |
| `hermes_state.py:744-751`                   | `update_system_prompt()`            | 持久化写入                            |
| `hermes_cli/config.py:821-822`              | 默认配置                            | `cache_ttl: "5m"`                     |

---

*最后更新：从源码分析生成，对应 Hermes Agent 源码 `~/.hermes/hermes-agent/`*
