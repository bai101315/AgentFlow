---
title: Tool Disclosure
created: 2026-06-22
updated: 2026-06-22
type: concept
tags: [tool-system, design-decision]
sources: [model_tools.py, tools/registry.py, toolsets.py]
confidence: high
---

# Tool Disclosure

Progressive tool disclosure — agent 只看到它**实际能用**的 tool，不是全量。
三层过滤架构。

## 三层过滤

### Layer 1: Toolset 选择

用户通过 `enabled_toolsets` / `disabled_toolsets` 控制哪些 toolset 参与解析。
`"hermes-cli"` 是一个复合 toolset，展开为 ~15 个子 toolset → ~70 个 tool 名称。

### Layer 2: check_fn 过滤

每个 tool 注册时附带一个 `check_fn()`。加载时执行：
- `check_fn()` 返回 `False` → tool 被移除（API key 未设置、后端不可用等）
- `check_fn()` 返回 `True` → tool 进入下一层
- 如果 check_fn 耗时（如网络探测），结果被 TTL 缓存

### Layer 3: Dynamic Schema

部分 tool 的 schema 在加载时动态修改：
- `delegate_task` description 反映实际的 `max_concurrent_children`
- `execute_code` 模式反映实际的沙箱配置

## 最终结果

~45 个 tool schema 以 OpenAI function-calling 格式返回。这个列表在 session 期间
是稳定的（不变化 = prompt cache 兼容）。

## 为什么搞这么复杂

如果 agent 看到 70 个 tool 但只有 45 个能用：
- 25 个不可用的 tool 会触发 tool-calling 失败循环
- System prompt 里多 25 个 tool schema → 输入的 token 成本增加
- Agent 会尝试调用不存在的功能 → 用户体验差

三层过滤确保 agent 只看到"现在能用的"。

## 关系

- [[tool-system]] — 过滤发生在 `get_tool_definitions()`
- [[prompt-caching]] — 稳定 tool list = cache 兼容
- [[agent-init]] — Phase 2 的 tool 加载流程
