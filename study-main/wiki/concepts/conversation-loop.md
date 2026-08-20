---
title: Conversation Loop
created: 2026-06-22
updated: 2026-06-24
type: concept
tags: [agent-loop, source-file, react]
sources: [agent/conversation_loop.py]
---

# Conversation Loop

Hermes agent 的核心执行引擎。一个同步的 **while tool-calling 循环**，不是 graph/DAG
架构（不像 LangChain 的 node+edge 模型）。

## 文件位置

`~/.hermes/hermes-agent/agent/conversation_loop.py:187` — `run_conversation()` 函数，
约 **4000 行**。

## ReAct 骨架（~20 行核心）

4000 行的本质是 20 行 ReAct 骨架 + 3980 行边界处理：

```python
# L598 — 循环入口
while (api_call_count < max_iterations
       and iteration_budget.remaining > 0)
       or _budget_grace_call:

    # L602 — 中断检查（/stop 或用户发新消息）
    if _interrupt_requested:
        break

    # L936 — 调用模型（含内层 retry + 限流等待 + 上下文压缩）
    response = _interruptible_api_call(client, messages, tools, ...)

    # L3105 — 有 tool_calls → 执行工具 → 结果塞回 messages → 循环继续
    if assistant_message.tool_calls:
        _execute_tool_calls(...)
        continue

    # L3419 — 没有 tool_calls → 最终回复 → 结束
    else:
        final_response = assistant_message.content
        break
```

**一个完整的 ReAct 迭代 = 调 API → 检查 tool_calls → 执行/回复。** 其他 3980
行全部是边界情况：网络错误重试、限流 backoff、上下文超长压缩、空回复恢复、
非法 JSON 修复、steer 注入、thinking prefill...

## 4000 行的结构骨架

```
L187-280   前置准备（stdio guard, session tag, skill origin, fallback 恢复）
L280-400   状态重置 & 内存水合（retry counter, budget, todo hydration, nudge 计数）
L400-525   构建 message list + preflight 上下文压缩
L526       ═══ 主循环入口 ═══
             │
L598       while (iteration < max AND budget > 0) or grace_call:
             │
             ├─ L600-660   中断检查 / steer 注入 / step callback
             ├─ L660-880   构建 API messages（system prompt + prefill + tools）
             ├─ L880-1050  KawaiiSpinner 启动
             ├─ L936-1270  内层 retry 循环 —— 实际 API 调用
             │   ├─ streaming / non-streaming 分支
             │   ├─ 网络错误 → backoff + retry
             │   ├─ context 超长 → 压缩 → 清空 history → retry
             │   ├─ rate limit → 等冷却 → retry
             │   └─ 成功 → 拿到 response
             ├─ L3105      工具调用处理
             │   ├─ 校验 tool name（防幻觉）
             │   ├─ 校验 JSON 参数
             │   ├─ 执行 handle_function_call()
             │   └─ 结果塞回 messages
             ├─ L3360      上下文压缩检查（should_compress）
             ├─ L3412      增量保存 session log
             ├─ L3419      无 tool_calls → 最终回复 → break
             └─ L3520      跑回 while 顶部
             │
L3858     保存 trajectory（如 save_trajectories 启用）
L3940     Plugin hook: transform_llm_output
L3960     Plugin hook: post_llm_call
L3990     提取 final_response
L4060     后台 review nudge（memory/skill 自动保存提示）
L4079     Plugin hook: on_session_end
L4095     return result
```

## 怎么看这 4000 行？

不要从头读到尾。按场景跟：

| 想看什么 | 从哪里开始 |
|----------|-----------|
| 正常 ReAct 路径 | L598 → L3105 → L3419，一条线 |
| API 调用细节 | L936-L1270 内层 retry 循环 |
| 上下文压缩 | L2300-L2650 compression_attempts |
| 限流处理 | L1289-L1294 sleep 等待 |
| 空回复恢复 | L3430-L3670 empty recovery |
| 非法 JSON 修复 | L3166-L3255 invalid_json |
| Tool 执行 | L3337 _execute_tool_calls() |

## 与 LangChain 的对比

| | LangChain | Hermes |
|---|---|---|
| 架构 | DAG graph (node + edge) | while 循环，模型自主决策 |
| 流程控制 | 开发者预定义拓扑 | LLM 通过 tool_calls 动态路由 |
| 状态管理 | State dict 沿 edge 传递 | OpenAI-format messages 数组 |
| 工具调用 | Tool 绑定到 node | OpenAI function-calling |
| 循环 | `add_conditional_edges` | 原生 while |

核心哲学：**所有流程编排都在 LLM 的 token 流里。** 循环逻辑只有两件事：
调 API、执行 tool 并追加结果。其他所有功能（guardrails、compression、memory
nudge、skill nudge）都是外围钩子。

## 循环外围钩子

在 while 循环的**内部**但在 API 调用**之前**，有一系列外围检查：

1. **Interrupt 检查** — `agent._interrupt_requested`（用户发新消息、`/stop`）
2. **Budget 检查** — `iteration_budget.consume()`，超限后给一次 grace call
3. **Step callback** — gateway 用这个 emit `agent:step` 事件
4. **Skill nudge** — `_iters_since_skill` 计数器，触发 auto-creation
5. **Memory nudge** — `_turns_since_memory` 计数器，触发 background review
6. **Pre-tool-call steer drain** — `/steer` 指令注入
7. **Context compression** — 逼近 token 上限时压缩

## 循环前置（每轮 run_conversation 开始时）

- 重置 retry 计数器（`_invalid_tool_retries`, `_empty_content_retries` 等）
- 重置 `_vision_supported` 标志
- Preflight context compression（如果 messages 已经超限）
- `pre_llm_call` 插件钩子
- Memory provider `on_turn_start()` + `prefetch_all()`

## 每次 API 调用前的消息处理

1. 修复 tool call 参数（`_sanitize_tool_call_arguments`）
2. 修复消息角色交替（`_repair_message_sequence`）
3. 注入 ephemeral context（memory prefetch + plugin context）到 user message
4. 复制 reasoning 到 `reasoning_content`
5. 剥离内部字段（`_thinking_prefill`, `call_id`, `response_item_id`）
6. 应用 Anthropic cache_control breakpoints
7. 剥离孤立的 tool 结果 / 补充缺失的 tool 结果
8. 删除 thinking-only turns
9. 规范化 JSON（sort_keys, separators）
10. Sanitize surrogate characters

## 关系

- [[aiagent]] — 拥有此循环的实例
- [[tool-system]] — `handle_function_call()` 调用的工具调度
- [[memory-system]] — 前置中的 memory nudge 和 prefetch
- [[prompt-caching]] — API 消息上的 cache_control 注入
- [[guardrail-system]] — `_tool_guardrails` 的检查点
- [[context-compression]] — 前置和循环中的压缩逻辑
