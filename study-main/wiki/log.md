# Wiki Log

> Chronological record of all wiki actions. Append-only.
> Format: `## [YYYY-MM-DD] action | subject`
> Actions: ingest, update, query, lint, create, archive, delete
> When this file exceeds 500 entries, rotate: rename to log-YYYY.md, start fresh.

## [2026-06-22] create | Wiki initialized
- Domain: Hermes Agent 内部架构
- Structure created with SCHEMA.md, index.md, log.md
- Wiki path: /home/bai/wiki

## [2026-06-22] create | Initial batch — 7 core pages
Created:
- entities/aiagent.md — AIAgent 核心类
- concepts/conversation-loop.md — while tool-calling 循环架构
- concepts/tool-system.md — 工具注册/过滤/调度链路
- concepts/tool-disclosure.md — 三层工具过滤
- concepts/memory-system.md — frozen snapshots + nudge 机制
- concepts/prompt-caching.md — 三层 prompt + SQLite 持久化
- concepts/guardrail-system.md — 行为约束系统

Updated:
- index.md — 7 个条目
- log.md — 此条目
- SCHEMA.md — Hermes Agent 内部架构领域定制

## [2026-06-24] ingest | hermesagent.org.cn — LLM Wiki 中文使用文档
Source: https://hermesagent.org.cn/docs/user-guide/skills/bundled/research/research-llm-wiki
Created:
- raw/articles/hermesagent-org-llm-wiki-zh.md — 原始文档缓存 (sha256: 1a0ff94d...)
- concepts/llm-wiki-usage.md — LLM Wiki 使用指南 (ingest/query/lint 流程、常见陷阱)
Updated:
- concepts/memory-system.md — 加 cross-link → [[llm-wiki-usage]]
- index.md — 新增 llm-wiki-usage 条目, Total pages: 7→8
