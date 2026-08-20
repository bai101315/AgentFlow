---
title: Prompt Caching
created: 2026-06-22
updated: 2026-06-29
type: concept
tags: [prompt-caching, design-decision]
sources:
  - agent/system_prompt.py
  - agent/conversation_loop.py
  - agent/prompt_caching.py
  - tools/memory_tool.py
  - agent/agent_runtime_helpers.py
  - agent/conversation_compression.py
  - hermes_state.py
---

# Prompt Caching

Hermes 的 prompt caching 由五个机制协同实现，保证 system prompt 在整个 session 的字节级稳定。

## 核心约束

LLM provider（Anthropic、DeepSeek、OpenAI）的 KV-cache 按**前缀字节相等**命中。
只要前缀中有一个字节不同，整个缓存就失效。

## 五个机制

### 1. 三层 Prompt 构造

`agent/system_prompt.py::build_system_prompt_parts()`

```
STABLE (never changes):
  SOUL.md → DEFAULT_AGENT_IDENTITY, tool guidance,
  tool-use enforcement, skills index, environment hints, platform hints

CONTEXT (changes between sessions):
  AGENTS.md / .cursorrules, system_message

VOLATILE (per-session, byte-stable):
  Memory frozen snapshot, USER.md frozen snapshot,
  date-only timestamp, model + provider
```

整段构建一次，缓存到 `agent._cached_system_prompt`，session 内所有 turn 复用。

### 2. Frozen Snapshot

`tools/memory_tool.py::MemoryStore`

Memory 有两个并行状态：
- `memory_entries` / `user_entries` — 实时状态，工具调用直接修改 + 保存到磁盘
- `_system_prompt_snapshot` — 冻结快照，只在 `load_from_disk()` 时拍摄

`format_for_system_prompt()` 返回冻结快照，不是实时状态。
Mid-session memory 写入不改变 system prompt，保护 prefix cache。

### 3. 时间戳精度降级

```python
timestamp_line = f"Conversation started: {now.strftime('%A, %B %d, %Y')}"
# "Monday, June 29, 2026" — date only, byte-stable for the full day
```

如果精确到分钟/秒，gateway 每个新 turn 重建 system prompt 时都会变化 → 100% cache miss。

### 4. SQLite 跨进程持久化

`agent/conversation_loop.py::_restore_or_build_system_prompt()`

Gateway 模式每个消息创建新 AIAgent 实例。第一次构建后把完整 system prompt 写入 `hermes_state.py` 的 sessions 表（`system_prompt` 列）。后续 turn 从 SQLite 恢复，字节完全一致复用。

三种状态：
- `missing` — 正常新 session，从头构建
- `null/empty` — 遗留/损坏，警告后重建
- `present` — 直接复用，cache 命中

CLI `--continue` 和 `/resume` 也受益。

### 5. Anthropic cache_control 显式标记

`agent/prompt_caching.py::apply_anthropic_cache_control()`

策略 `system_and_3`：system prompt + 最后 3 条非 system 消息，最多 4 个 breakpoint。

两种布局：
- Native Anthropic：`cache_control` 在 content block 内部
- OpenRouter/第三方：`cache_control` 在 message 顶层

启用决策在 `agent/agent_runtime_helpers.py::anthropic_prompt_cache_policy()`：
Claude + Anthropic/OpenRouter/兼容网关 → 启用；Qwen + Alibaba/OpenCode → 启用；MiniMax → 启用；其他 → 不启用。

TTL 默认 `5m`，可选 `1h`（一次写入 2x 成本，适合长 pause 的 session）。

## 完整执行流程（Gateway 收消息为例）

```
1. Gateway 收到消息 → 从 SQLite 恢复 conversation_history
2. 创建 AIAgent → agent_init.py 调用 _anthropic_prompt_cache_policy()
3. run_conversation() 被调用
   → if _cached_system_prompt is None:
       _restore_or_build_system_prompt()
       ├── 从 SQLite 读 system_prompt → present → 直接复用
       └── 否则 → build_system_prompt_parts()
           ├── STABLE: SOUL.md + tool guidance + skills index + env hints + platform hints
           ├── CONTEXT: system_message + context files（TERMINAL_CWD 下查找）
           └── VOLATILE: memory frozen snapshot + USER.md snapshot + date-only timestamp
       → 写入 SQLite
   → active_system_prompt = _cached_system_prompt
4. 每个 API 调用
   → api_messages = [system] + history + [user_msg]
   → if _use_prompt_caching: apply_anthropic_cache_control(api_messages)
   → 发送到 LLM
```

## Cache 失效

唯一合法的 cache breaker：**context compression**。

`agent/conversation_compression.py:358` 调用 `invalidate_system_prompt()`：
清空 `_cached_system_prompt` + `memory_store.load_from_disk()`（重载 memory）。

代价：compression 后第一个 turn 全量重算。必要——不压缩会 context overflow。

## Provider 缓存策略

| Provider | 缓存机制 | Cache-hit 定价 |
|----------|---------|----------------|
| Anthropic 原生 | 显式 cache_control | 满价 10% |
| Claude + OpenRouter | 显式 cache_control | 满价 10% |
| DeepSeek | 自动前缀匹配 | 满价 50% |
| OpenAI | 自动前缀匹配 | 满价 50% |
| Qwen + Alibaba/OpenCode | 显式 cache_control | 低于满价 |

## 实际效果

10-turn session, 15K system prompt（典型场景：带 AGENTS.md 的项目）：

| Turn | 发送 tokens | Cache 命中 | 新计算 | 成本 vs 全额 |
|------|------------|-----------|--------|-------------|
| 1 | 15,050 | 0 | 15,050 | 100% |
| 2 | 15,900 | 15,050 | 850 | ~20% |
| 10 | 18,200 | 17,300 | 900 | ~20% |

Turn 2 起成本降低 ~80%，延迟降低 ~80%。

## 当前 session 实际数据

当前 session（deepseek-v4-pro, CLI, CWD=/home/bai）：
- System prompt: ~4,600 tokens（19,600 字符）
- AGENTS.md 未加载（CWD 不在 git 仓库内，无 context files）
- Memory: 2,037/2,200 chars (92%)
- User profile: 1,365/1,375 chars (99%)

完整 system prompt dump: `/home/bai/hermes-full-system-prompt.txt`

## 关键源码

- `agent/system_prompt.py:60-284` — `build_system_prompt_parts()`
- `agent/system_prompt.py:287-303` — `build_system_prompt()`
- `agent/system_prompt.py:306-314` — `invalidate_system_prompt()`
- `agent/conversation_loop.py:85-184` — `_restore_or_build_system_prompt()`
- `agent/conversation_loop.py:415-826` — 从缓存取 prompt → 注入 cache_control → 发送
- `agent/prompt_caching.py:1-79` — `apply_anthropic_cache_control()` system_and_3 策略
- `tools/memory_tool.py:126-142` — `load_from_disk()` 拍摄冻结快照
- `tools/memory_tool.py:361-372` — `format_for_system_prompt()` 返回冻结快照
- `agent/agent_runtime_helpers.py:1086-1188` — `anthropic_prompt_cache_policy()`
- `agent/agent_init.py:400-413` — policy 调用 + TTL 设置
- `agent/conversation_compression.py:358` — compression 触发 invalidate
- `hermes_state.py:196` — sessions 表 `system_prompt` 列
- `hermes_state.py:744-751` — `update_system_prompt()` 持久化写入

## 关系

- [[system-prompt]] — system prompt 的三层结构和实际大小
- [[memory-system]] — frozen snapshots 被设计为 session-stable
- [[conversation-loop]] — 循环中组装 api_messages
- [[context-compression]] — 唯一合法的 cache 失效触发点
- [[aiagent]] — `_cached_system_prompt` 属性
