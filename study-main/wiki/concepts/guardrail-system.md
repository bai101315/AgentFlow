---
title: Guardrail System
created: 2026-06-22
updated: 2026-06-22
type: concept
tags: [guardrail, agent-loop]
sources: [run_agent.py, agent/conversation_loop.py, agent/agent_init.py]
---

# Guardrail System

Agent 行为约束系统。不阻止操作，而是检测异常模式并发出警告或硬停止。

## 组件

### ToolCallGuardrailController

- `warnings_enabled` — 警告是否启用
- `hard_stop_enabled` — 硬停止是否启用（默认关闭）
- `exact_failure_warn_after` — 完全相同的 tool 连续失败 N 次后警告（默认 2）
- `same_tool_failure_warn_after` — 同类 tool 连续失败 N 次后警告（默认 3）
- `no_progress_warn_after` — 连续 N 轮无实质性进展后警告（默认 2）

### IterationBudget

单次 `run_conversation()` 最多 90 次 API 调用。超限后给一次 grace call。
（注意：是 API 调用次数，不是 tool 调用次数——一次 API 调用可以返回多个
 tool_calls。）

### 秘密脱敏

`security.redact_secrets` 控制是否在 tool 输出中脱敏 API key / token。
**默认关闭。** 开启后需重启 session（import 时快照，动态环境变量不生效）。

### 命令审批

`approvals.mode` 控制破坏性命令是否需要用户确认：
- `manual` — 总是提示（默认）
- `smart` — 辅助 LLM 自动批准低风险命令
- `off` — 跳过所有提示（= `--yolo`）

## 在 Agent 循环中的位置

- 每轮开始时 `agent._tool_guardrails.reset_for_turn()`
- Tool 执行后 guardrail 检查失败模式
- 如果 `hard_stop_enabled` 且达到阈值，break 循环

## 关系

- [[aiagent]] — 持有 `_tool_guardrails`, `iteration_budget`
- [[conversation-loop]] — 循环中的 guardrail 检查点
- [[agent-init]] — Phase 3 的 guardrail 初始化
