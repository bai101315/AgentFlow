---
title: LLM Wiki 使用指南
created: 2026-06-24
updated: 2026-06-24
type: concept
tags: [skill-system, definition, pitfall]
sources: [raw/articles/hermesagent-org-llm-wiki-zh.md]
---

# LLM Wiki 使用指南

基于 Karpathy 的 LLM Wiki 模式构建的持久化知识库工具。用相互链接的 Markdown 文件积累知识，
一次编译、持续使用。交叉引用已存在，矛盾已标记，综合内容反映所有摄入信息。

**分工**: 人类策划来源+指导分析，代理负责总结、交叉引用、归档、维护一致性。

## vs [[memory-system]]：Wiki vs Memory

| | Memory | LLM Wiki |
|---|---|---|
| 注方式入 | 每轮自动 push 到 system prompt | 需手动加载 skill |
| 本类型数 | 短文事实 (姓名、路径、偏好) | 结构长文 (实体/概念/交叉引用) |
| 存储 | SQLite (FTS5 全文搜索) | 纯 Markdown 文件 |
| 维护 | Agent 在 nudge 后自动调用 memory 工具 | 需手动触发 ingest |

## 三种核心操作

### 1. Ingest (摄取)

**触发**: 给 URL、PDF、粘贴文本 → "加到 wiki"

步骤:
1. 原始内容 → `raw/` 目录 (不可变，加 sha256 做 drift detection)
2. 和用户讨论要点 (cron 上下文跳过)
3. 查已有页面避免重复 (index + search_files)
4. 按 Page Threshold 创建/更新 wiki 页面
   - 每页最少 2 个 `[[wikilinks]]`
   - 标签只用在 SCHEMA.md 分类法里的
   - 综合 3+ 来源的段落加 provenance `^[raw/...]`
5. 更新 index.md + log.md
6. 报告所有变更

### 2. Query (查)

**触发**: 问 wiki 覆盖域的问题

步骤:
1. 读 index.md 定位相关页面
2. 100+ 页 wiki 用 search_files 补充
3. 读页面，综合回答
4. 值得保留的答案写回 queries/ 或 comparisons/
5. 更新 log.md

### 3. Lint (健康检查)

**触发**: "lint the wiki" / "wiki 有没有问题"

检查项 (按严重程度): 损坏链接 > 孤立页面 > 来源漂移 > 争议页面 > 过时内容 > 风格问题

## 每次会话的启动流程 (CRITICAL)

**不加载 skill → wiki 只是死文件目录**

```
方式 A: hermes -s llm-wiki -c         ← 推荐
方式 B: /skill llm-wiki                ← 会话中手动加载
方式 C: "加载 llm-wiki，帮我查一下..."  ← 一句话搞定
```

加载后 → 先定向 (SCHEMA + index + log) → 再操作

## 为何觉得不会用 (4 个原因)

1. **加载是手动门槛** — 不像 memory 自动推送。忘记 /skill llm-wiki → wiki 消失
2. **加载时机模糊** — 不知道何时该用。LLM 不会自动"觉得 wiki 有答案然后主动加载"
3. **Ingest vs Query 体验不对称** — ingest 有明确"给链接"的动作感，query 需元认知负担想"wiki 存过了吗？"
4. **没有"先查 wiki"的默认行为** — 理想是 agent 判断 wiki 有内容就优先用，实际无此自动化

## 常见陷阱
1. 改 `raw/` 中的文件 — 不允许
2. 跳过了定向 — 产生重复页面、遗漏交叉引用
3. 不更新 index.md + log.md — wiki 退化
4. 为短暂提及建页面 — 脚注里出现一次不值得建实体页
5. 孤立页面 (无 wikilinks) — 每页必须 ≥2 个出站链接
6. Frontmatter 缺失 — 无法搜索、过滤、检测过期
7. 自由标签 — 先在 SCHEMA.md 加分类，再使用
8. 页面过长 — >200 行拆分
9. 不确认就大量更新 — 影响 10+ 页时先问
10. 日志不轮转 — log.md 超 500 条后拆分
11. 静默覆盖矛盾 — 标记 contradicted + 两方都保留
12. WSL + Windows Obsidian — wiki 放 Windows 侧并 symlink 回 WSL
