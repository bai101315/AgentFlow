# AI Agent 工程学习与面试知识库

> 从 Agent 原理、运行时工程到源码分析与面试表达的一套个人学习笔记。

这个仓库记录我对 **AI Agent 工程化、AgentFlow 项目设计、Hermes Agent 源码、技术面试和算法基础** 的持续学习与复盘。内容以 Markdown 文档为主，辅以少量用于验证 Prompt Cache、工具调用和持久化机制的实验脚本。

它不是一个可直接部署的软件项目，而是一张不断更新的知识地图。当前主线是理解如何把“能调用工具的 LLM”构建成一个 **可恢复、可治理、可记忆、可检索、可观测、可扩展** 的 Agent Runtime。

## 从这里开始

| 你的目标 | 推荐入口 |
| --- | --- |
| 快速了解 AgentFlow 项目 | [项目介绍](八股+项目理解/项目介绍.md) -> [AgentFlow 面试复习](八股+项目理解/AgentFlow_面试复习.md) |
| 系统学习 Agent 工程机制 | [Function Calling](八股+项目理解/01_function_calling.md) -> [MCP](八股+项目理解/03_MCP.md) -> [Multi-Agent](八股+项目理解/05_multi-agent.md) |
| 研究开源 Agent 的源码设计 | [Hermes Agent 核心](hermes_study/hermes_agent核心.md) -> [Agent 循环](hermes_study/agent.md) -> [工具系统](hermes_study/tool.md) |
| 准备项目面试与追问 | [实习项目问题](八股+项目理解/实习项目问题.md) -> [面试记录](面经/面经.md) -> [整理后的回答](面经/答案/) |
| 复习算法与计算机基础 | [算法模板](algorithm/) -> [Python 八股](八股+项目理解/python八股.md) -> [强化学习](rl/) |

## 知识地图

### AgentFlow 项目主线

项目笔记围绕一个基于 LangChain / LangGraph 的本地多 Agent Runtime 展开，重点不是 UI，而是 Agent 执行链路的工程治理。

主要覆盖：

- **运行时编排**：Agent 主循环、Middleware、状态传递与错误处理。
- **工具治理**：Function Calling、MCP、Tool Group、Deferred Tool 与渐进式披露。
- **多 Agent 协作**：任务分解、Sub-Agent、上下文隔离与结果汇总。
- **状态与记忆**：Checkpoint、长期 Memory、Session Search 及三者的职责边界。
- **上下文工程**：System Prompt、Prompt Cache、上下文压缩与信息载体。
- **可靠性与安全**：Sandbox、循环检测、澄清机制、工具异常兜底。
- **可观测性**：Trace、Model Call、Tool Call、Token 与缓存命中率分析。
- **能力演化**：Skill 的渐进加载、管理与可复用工作流沉淀。

建议先读 [项目介绍](八股+项目理解/项目介绍.md) 建立整体认识，再用 [AgentFlow 面试复习](八股+项目理解/AgentFlow_面试复习.md) 串联各模块。

### Agent 工程专题

`八股+项目理解/` 按机制拆解 Agent 系统，适合系统学习和面试前查漏补缺。

| 阶段 | 主题 |
| --- | --- |
| 基础 | [Function Calling](八股+项目理解/01_function_calling.md) · [工具调用原理](八股+项目理解/02.md) · [MCP](八股+项目理解/03_MCP.md) |
| 编排 | [Skill](八股+项目理解/04_skill.md) · [Multi-Agent](八股+项目理解/05_multi-agent.md) · [三种 Agent 范式](八股+项目理解/06_三种范式区别.md) · [A2A](八股+项目理解/07_A2A.md) |
| 状态 | [短期记忆](八股+项目理解/08_memory.md) · [信息载体](八股+项目理解/09_信息载体.md) · [长期记忆](八股+项目理解/12_memory.md) · [Session Search](八股+项目理解/17_session_search.md) |
| 工程 | [中间件](八股+项目理解/10_中间件.md) · [Sandbox](八股+项目理解/11_sandbox.md) · [Sub-Agent](八股+项目理解/13_sub-agent.md) · [工程学习](八股+项目理解/18_工程学习.md) |
| 扩展 | [提示词模板](八股+项目理解/14提示词模板.md) · [ACP](八股+项目理解/15_ACP.md) · [RPC](八股+项目理解/16_RPC.md) · [RAG](八股+项目理解/RAG.md) |

### Hermes Agent 源码研究

`hermes_study/` 关注概念如何落到真实代码，包括 Agent Loop、工具注册与发现、MCP、Memory、Sandbox、Sub-Agent、Prompt Cache、日志和自我改进机制。

推荐阅读顺序：

1. [Hermes Agent 核心概览](hermes_study/hermes_agent核心.md)
2. [Agent 核心循环](hermes_study/agent.md)
3. [工具注册、发现与渐进式披露](hermes_study/tool.md)
4. [MCP 工具治理](hermes_study/MCP_tool.md)
5. [Memory](hermes_study/memory.md)、[中间件](hermes_study/middle.md) 与 [Sandbox](hermes_study/sandbox.md)
6. [Sub-Agent](hermes_study/sub-agent.md) 与 [多 Agent 场景](hermes_study/multi_agent_feature.md)
7. [Prompt Cache](hermes_study/prompt_cache.md)、[可观测性](hermes_study/log.md) 与 [Self-Improving](hermes_study/self-improving.md)

更适合检索和交叉阅读的版本见 [Hermes Wiki](wiki/index.md)。Wiki 按实体、概念和原始资料组织，并维护独立的 [内容规范](wiki/SCHEMA.md)。

### 面试与基础训练

- `面经/`：真实面试问题、追问方向、Prompt 和整理后的回答稿。
- `algorithm/`：二分、动态规划、图论、滑动窗口、数据结构、链表、树与回溯。
- `rl/`：强化学习基本概念、Bellman 方程与最优价值函数。
- `当前的trending/`：Hermes、Pi、OpenCode、DeerFlow 等 Agent 产品与框架的横向观察。

## 仓库结构

```text
.
|-- 八股+项目理解/         # AgentFlow、Agent 机制与项目面试复习
|-- hermes_study/          # Hermes Agent 源码研究笔记
|-- wiki/                  # Hermes Agent 结构化知识库
|-- 当前的trending/        # Agent 框架与产品趋势对比
|-- 面经/                  # 面试记录、问题与回答稿
|-- algorithm/             # 算法与数据结构模板
|-- rl/                    # 强化学习基础
|-- test/                  # 独立验证脚本与实验数据
`-- assets/images/         # 文档使用的公共图片
```

## 使用方式

这个仓库以阅读和检索为主，可以直接在 GitHub 或支持 Markdown 的编辑器中浏览。

```bash
git clone git@github.com:bai101315/study.git
cd study
```

建议根据问题搜索关键词，而不是只按目录顺序阅读。例如：

```bash
rg "Prompt Cache|Checkpoint|Session Search"
rg "MCP|tool_search|DeferredToolRegistry" 八股+项目理解 hermes_study wiki
```

`test/` 下的脚本用于独立机制验证，没有统一的依赖文件或运行入口。执行前应先检查对应脚本的导入、环境变量和外部服务配置。

## 内容维护

- Agent 通用机制、AgentFlow 项目理解和面试表达放在 `八股+项目理解/`。
- Hermes 源码阅读笔记放在 `hermes_study/`；结构化条目遵循 `wiki/SCHEMA.md`。
- 原始面试记录放在 `面经/`，整理后的回答放在 `面经/答案/`。
- 公共图片放在 `assets/images/`，单篇文档专用图片可以保留在文档同级目录。
- 新增、移动或删除文档后，同步维护 README 和相关索引，避免失效链接。
- 笔记中的源码行为可能随上游版本变化，重要结论应结合对应版本源码和官方文档验证。

## 说明

本仓库用于个人学习、源码研究和面试复盘，内容会随着实践和理解持续修订。部分文档保留了探索过程中的推导和取舍，它们既是知识总结，也是后续继续验证问题的线索。
