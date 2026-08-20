---
title: Memory System
created: 2026-06-22
updated: 2026-06-22
type: concept
tags: [memory-system, design-decision, pitfall]
sources: [tools/memory_tool.py, agent/memory_manager.py, agent/conversation_loop.py]
---

# Memory System

跨 session 的持久记忆。两层架构：built-in（markdown 文件） + 外部 provider（可选）。

## 文件位置

| 文件 | 角色 |
|------|------|
| `tools/memory_tool.py` | `MemoryStore` — built-in memory，frozen snapshots |
| `agent/memory_manager.py` | `MemoryManager` — 编排 built-in + 外部 provider |
| `agent/conversation_loop.py` | nudge 逻辑 + prefetch 注入 |

## Built-in Memory: Frozen Snapshots

Built-in memory 存储在 `~/.hermes/memories/MEMORY.md` 和 `USER.md`。

**关键设计：frozen snapshots。** 在 `AIAgent` 构造时（`init_agent()` Phase 4），
`MemoryStore.load_from_disk()` 读取文件内容并捕获一个**冻结快照**。这个快照
被注入到 system prompt 的 VOLATILE 层。

**快照在整个 session 期间不会自动更新。** 即使 agent 调用了 `memory` 工具并写入了
MEMORY.md 磁盘文件，注入到 prompt 里的仍然是初始快照。这是为了 prompt caching —
system prompt 内容稳定 = cache 命中。下一次 session 会加载新的快照。

## Memory Nudge

memory 工具不是 agent 主动调用的。大多数 LLM（尤其是 DeepSeek V4）即使
system prompt 里有 `MEMORY_GUIDANCE`，也很少主动调用 `memory` 工具。

Hermes 通过 **nudge 机制**来解决这个问题：

- 每 `memory.nudge_interval`（默认 10）轮对话后，触发 background memory review
- 在 CLI 模式下，`_turns_since_memory` 计数器在每次 `hermes` 启动时重置为 0
- **这是 CLI 模式下记忆不更新的根因** — 如果 session 长度 < nudge_interval，
  background review 永远不会触发

验证命令：
```bash
# 检查 session 中是否有 memory 工具调用
cd ~/.hermes/sessions && for f in session_*.json; do
  python3 -c "..."
done
```

## 外部 Memory Providers

通过 `memory.provider` 配置启用（如 Honcho、Mem0、Supermemory）。
`MemoryManager` 在每轮开始时调用 `prefetch_all(query)` 获取上下文，
结果注入到 user message 中（不是 system prompt，以保持 prompt cache）。

## 关系

- [[aiagent]] — 持有 `_memory_store` / `_memory_manager`
- [[conversation-loop]] — 前置中的 nudge + prefetch
- [[prompt-caching]] — frozen snapshots 的设计动机
- [[agent-init]] — Phase 4 的 memory 加载
- [[llm-wiki-usage]] — LLM Wiki vs Memory 对比参考
