---
title: Tool System
created: 2026-06-22
updated: 2026-06-22
type: concept
tags: [tool-system, source-file]
sources: [model_tools.py, tools/registry.py, toolsets.py]
---

# Tool System

工具注册、发现、过滤、调度的完整链路。三条核心文件：

## 文件位置

| 文件 | 角色 |
|------|------|
| `tools/registry.py` | 中心注册表 — 无依赖，被所有 tool 文件 import |
| `tools/*.py` | 各个 tool 实现 — 在 import 时调用 `registry.register()` |
| `toolsets.py` | Toolset 定义 — `_HERMES_CORE_TOOLS` 列表 |
| `model_tools.py` | Tool 编排 — `get_tool_definitions()` + `handle_function_call()` |

## 三层工具过滤（Progressive Disclosure）

1. **Toolset 层** — `enabled_toolsets` 决定哪些 toolset 参与解析。`"hermes-cli"`
   是一个复合 toolset，展开为 ~15 个子 toolset，最终解析出 ~70 个具体 tool 名称。

2. **check_fn 层** — 每个 tool 注册时带一个 `check_fn()`。工具被加载时，
   `check_fn()` 检查前置条件（API key 是否设、后端是否可用等），不满足条件的
   tool 从最终定义中被移除。

3. **Dynamic schema 层** — 部分 tool 的 schema 在加载时动态修改。例如
   `delegate_task` 的 description 会反映实际的 `max_concurrent_children` 值。

最终结果：~45 个 tool schema（OpenAI function-calling 格式）。

## handle_function_call() 流程

`model_tools.py:741` — tool 调度的中心入口：

1. 类型强制转换（`coerce_tool_args` — 将字符串 "42" 转为 int 42）
2. 检查是否为 agent-loop-only tool（拒绝）
3. `pre_tool_call` 插件钩子（插件可以 block 调用）
4. ACP edit approval 检查（Zed/VS Code 集成）
5. 非读取 tool 调用时重置 read-loop tracker
6. 查找 registry 中的 handler 并执行
7. 检查跨 profile 写入保护
8. `post_tool_call` 插件钩子

所有 handler 必须返回 JSON 字符串。

## Toolset 定义

`toolsets.py` 中定义：
- **Basic toolsets**: web, terminal, file, vision, browser 等
- **Composite toolsets**: `hermes-cli` 展开为多个子 toolset
- **Scenario toolsets**: 特定场景的预设组合

## 关系

- [[aiagent]] — 持有 `tools` / `valid_tool_names`
- [[conversation-loop]] — 在循环中调用 `handle_function_call()`
- [[tool-disclosure]] — 三层过滤架构详解
- [[agent-init]] — Phase 2 的 tool 加载
