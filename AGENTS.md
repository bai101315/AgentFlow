# AGENTS.md — AgentFlow 项目规则

## 🚨 强制规则：未读代码禁止回答

在回答任何关于代码的问题、给出任何建议或结论之前，你必须完成以下步骤：

### 必做步骤（缺一不可）

1. **读取相关源码** — 用 Read 工具读取你将要讨论的文件。没有读过源码 = 不能谈论该代码。
2. **给出代码证据** — 回答必须包含具体文件路径和行号（如 `backend/agents/review_agent.py:142`）以及关键代码片段。
3. **区分事实与推测** — 如果你没有读到相关代码，明确说"我没有读过这部分代码，无法确认"。

### 禁止行为

- ❌ 没有读过文件就评论代码质量、给出修改建议
- ❌ 回答"为什么"、"是什么"、"是否有问题"时不引用具体代码
- ❌ 用"可能"、"应该"、"我记得"替代实际代码证据
- ❌ 声称已修复/已通过但没有工具输出支撑
- ❌ 复述你的推测作为事实

### 回答前自检清单

在输出回答之前，必须确认以下条件全部满足：

- [ ] 我已用 Read 工具读取了相关源码文件
- [ ] 我的回答基于实际读取的代码，不是基于记忆或推测
- [ ] 回答中包含具体的文件路径和行号
- [ ] 如果无法确认，已明确说明证据缺口

---

## 项目背景（快速定位）

- **框架**: DeerFlow 风格 agent 框架
- **核心目录**:
  - `backend/agents/` — 中间件、review_agent、memory
  - `backend/skill/` — manager / curator / usage / validation
  - `backend/observability/` — 可观测性
- **配置**: `config.yaml`
- **数据**: `.agentflow/` (observability.db, events.jsonl 等)
- **Self-improving 机制**: skill_manage 工具 / background review agent / curator / memory 中间件

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
