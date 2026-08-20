---
source_url: https://hermesagent.org.cn/docs/user-guide/skills/bundled/research/research-llm-wiki
ingested: 2026-06-24
sha256: 1a0ff94dd799c4e1b0fcba3b19a8e2ca459878ad052e57a9dda744c0f805e4b2
---

# Llm Wiki — Karpathy 的 LLM Wiki

Karpathy 的 LLM Wiki — 构建并维护一个持久化、相互链接的 Markdown 知识库。
基于 Andrej Karpathy 的 LLM Wiki 模式。

与传统的 RAG（每次查询都从头重新发现知识）不同，Wiki 一次性编译知识并保持其最新状态。
交叉引用已经存在。矛盾之处已被标记。综合内容反映了所有摄取的信息。

分工：人类负责策划来源和指导分析。代理负责总结、交叉引用、归档并保持一致性。

## 何时激活此技能

当用户执行以下操作时使用此技能：
- 要求创建、构建或启动 Wiki 或知识库
- 要求将来源摄取、添加或处理到其 Wiki 中
- 提出问题且配置的路径下存在现有 Wiki
- 要求对其 Wiki 进行 lint 检查、审计或健康检查
- 在研究背景下提及他们的 Wiki、知识库或"笔记"

## Wiki 位置

通过 WIKI_PATH 环境变量设置（在 ~/.hermes/.env 中）。如果未设置，默认为 ~/wiki。

## 架构：三层结构

wiki/
├── SCHEMA.md           # 约定、结构规则、领域配置
├── index.md            # 分节内容目录，每行带一行摘要
├── log.md              # 按时间顺序的操作日志（仅追加，按年轮转）
├── raw/                # 第一层：不可变的原始素材
│   ├── articles/       # 网页文章、剪报
│   ├── papers/         # PDF、arxiv 论文
│   ├── transcripts/    # 会议记录、访谈
│   └── assets/         # 图片、来源引用的图表
├── entities/           # 第二层：实体页面（人、组织、产品、模型）
├── concepts/           # 第二层：概念/主题页面
├── comparisons/        # 第二层：并列分析
└── queries/            # 第二层：值得保留的查询结果归档

第一层 — 原始来源：不可变，代理只读。
第二层 — Wiki：代理拥有的 Markdown 文件，由代理创建、更新和交叉引用。
第三层 — Schema：SCHEMA.md 定义结构、约定和标签分类法。

## 恢复现有 Wiki（每次会话必须）

① 阅读 SCHEMA.md — 理解领域、约定和标签分类法
② 阅读 index.md — 了解存在哪些页面及其摘要
③ 扫描最近的 log.md — 阅读最后 20-30 条条目

## SCHEMA.md 模板关键要素

- 文件名：小写、连字符、无空格
- 每个 wiki 页面必须以 YAML frontmatter 开头
- 使用 [[wikilinks]] 链接页面（每页至少 2 个出站链接）
- 更新页面时必须提升 updated 日期
- 每个新页面必须添加到 index.md 的正确部分
- 每个操作必须追加到 log.md

### Frontmatter
- title, created, updated, type (entity|concept|comparison|query|summary)
- tags: [来自分类法]
- sources: [raw/articles/source-name.md]
- 可选：confidence (high|medium|low), contested (true), contradictions ([page-name])

### Page Thresholds
- 当实体/概念在 2+ 来源中出现或对某一来源至关重要时创建页面
- 当来源提及已覆盖的内容时添加到现有页面
- 不要为短暂提及或次要细节创建页面
- 页面超过 ~200 行时拆分
- 内容完全被取代时归档到 _archive/

## 三种核心操作

### 1. 摄取 (Ingest)
① 捕获原始来源（URL → web_extract → raw/articles/）
② 与用户讨论要点（自动化/cron 上下文中跳过）
③ 检查已有内容（search index.md + search_files）
④ 编写或更新页面（遵循 Page Thresholds + 交叉引用 ≥2 + 标签从分类法中来）
⑤ 更新导航（index.md + log.md）
⑥ 报告变更

### 2. 查询 (Query)
① 读 index.md 识别相关页面
② 大 wiki(100+页)需 search_files 补充
③ 读相关页面
④ 综合答案，引用 [[page]]
⑤ 归档有价值的答案到 queries/ 或 comparisons/
⑥ 更新 log.md

### 3. Lint（健康检查）
检查项目：孤立页面、损坏 wikilink、索引完整性、frontmatter 验证、过时内容、矛盾、质量信号、
来源漂移、页面大小、标签审计、日志轮转，按严重程度分组报告。

## 常见陷阱
1. 切勿修改 raw/ 中的文件
2. 始终先定向（SCHEMA + index + log）
3. 始终更新 index.md 和 log.md
4. 不为短暂提及创建页面
5. 不创建无交叉引用的页面
6. Frontmatter 必需
7. 标签必须来自分类法
8. 保持页面可扫描（30秒可读）
9. 大规模更新前需确认
10. 日志轮转（500条目后）
11. 明确处理矛盾，不要静默覆盖
12. WSL + Windows Obsidian：移到 Windows 侧并 symlink 回来
