---
title: System Prompt
created: 2026-06-29
updated: 2026-06-29
type: concept
tags: [system-prompt, design-decision]
sources:
  - agent/system_prompt.py
  - agent/prompt_builder.py
  - tools/memory_tool.py
  - agent/conversation_loop.py
---

# System Prompt

Hermes 的 system prompt 是三层运行时组装的结果。**不是**一个静态文本文件。

## 三层结构

| 层级 | 内容 | 何时变化 |
|------|------|---------|
| STABLE | SOUL.md identity, tool guidance, skills index, environment hints, platform hints | 几乎不变（skill 增删后刷新） |
| CONTEXT | AGENTS.md / .cursorrules, system_message | 按项目变化 |
| VOLATILE | Memory 冻结快照, USER.md 快照, 时间戳 | 每次新 session 重建 |

## 组装入口

`agent/system_prompt.py::build_system_prompt_parts()` — 三个独立函数返回三段，用 `\n\n` 拼接。

`build_system_prompt()` 整段缓存到 `agent._cached_system_prompt`，session 内所有 turn 复用同一个字符串。只有 context compression 后 `invalidate_system_prompt()` 触发重建。

## AGENTS.md / .cursorrules 的查找规则

从 `TERMINAL_CWD`（gateway 模式）或当前工作目录（CLI 模式）向上查找，直到 git root 或文件系统根。在目录下找以下文件：

- `.hermes.md` / `HERMES.md`（优先）
- `AGENTS.md`
- `.cursorrules`

**如果终端不在 git 仓库内**（如 `/home/bai` 不是 git 仓库），只查当前目录，不递归向上到 `/`。

**实测**：当前 session 的 `TERMINAL_CWD=/home/bai`，没有 git root，也没有上述任何文件，因此 CONTEXT 层为空。`~/.hermes/hermes-agent/AGENTS.md` 存在但不被加载——因为 CWD 不是那个目录。

## 实际大小

当前 session（deepseek-v4-pro, CLI）system prompt 约 **4,600 tokens**（19,600 字符）。

如果有 AGENTS.md（如 cd 到大型项目），会增加 10-15K tokens。

完整内容 dump 见 `/home/bai/hermes-full-system-prompt.txt`。

## 时间戳精度降级

```python
timestamp_line = f"Conversation started: {now.strftime('%A, %B %d, %Y')}"
# "Monday, June 29, 2026" — 仅日期，一天内字节稳定
# 不用 "%H:%M:%S" —— 那样每分钟都会变，破坏 prefix cache
```

## 动态条件注入

以下内容是否注入取决于运行时的工具/模型/平台：

| 内容 | 注入条件 |
|------|---------|
| MEMORY_GUIDANCE | `memory` 工具已加载 |
| SESSION_SEARCH_GUIDANCE | `session_search` 工具已加载 |
| SKILLS_GUIDANCE | `skills_list`/`skill_view`/`skill_manage` 任一加载 |
| TOOL_USE_ENFORCEMENT_GUIDANCE | model 匹配 `TOOL_USE_ENFORCEMENT_MODELS` |
| OPENAI_MODEL_EXECUTION_GUIDANCE | model 含 `gpt`/`codex`/`grok` |
| GOOGLE_MODEL_OPERATIONAL_GUIDANCE | model 含 `gemini`/`gemma` |
| KANBAN_GUIDANCE | `kanban_show` 工具已加载 |
| COMPUTER_USE_GUIDANCE | `computer_use` 工具已加载 |
| Skills index | skills 工具已加载 |
| Environment hints | 始终（WSL/Termux 检测） |
| Platform hint | 根据 platform 参数 |

## 关键源码

- `agent/system_prompt.py:60-284` — `build_system_prompt_parts()`
- `agent/system_prompt.py:287-303` — `build_system_prompt()`
- `agent/prompt_builder.py:134-142` — `DEFAULT_AGENT_IDENTITY`
- `agent/prompt_builder.py:144-186` — 各 GUIDANCE 常量
- `agent/prompt_builder.py:254-267` — `TOOL_USE_ENFORCEMENT_GUIDANCE`
- `agent/prompt_builder.py:992-1460` — `build_skills_system_prompt()`
- `tools/memory_tool.py:361-372` — `format_for_system_prompt()` 冻结快照

## 关系

- [[prompt-caching]] — system prompt 的稳定性是缓存命中的前提
- [[memory-system]] — frozen snapshot 机制保护 system prompt 不被 mid-session memory 写入改变
- [[conversation-loop]] — `_restore_or_build_system_prompt()` 恢复/构建入口
- [[context-compression]] — 唯一合法的 cache invalidation 触发点
