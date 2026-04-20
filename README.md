# LeetCode 刷题助手项目文档

## 1. 项目概述

本项目是一个基于 DeerFlow/LangGraph 的本地智能体系统，目标是将 LeetCode 刷题过程自动化为“数据采集 -> 多维分析 -> 总结落盘”闭环。

核心功能与特性：
- LeetCode 数据接入：通过 MCP server 读取用户状态、题目、提交、题解等信息。
- 刷题能力分析：输出做题数量、难度分布、知识点覆盖、活跃趋势、薄弱点与改进建议。
- 每日总结生成：按固定 Markdown 模板生成总结文档，包含题目、关键分析、代码、总体复盘与次日计划。
- 可配置 Agent：支持 `leetcode-assis` 自定义人格（SOUL）与工具权限。
- 本地可执行工具：支持 `ls/read_file/write_file/str_replace/bash`，并可配置本机执行 bash。
- 线程化工作区：每个 thread 拥有独立的 `workspace/uploads/outputs`，便于隔离会话产物。
- 记忆系统：支持全局记忆和按 agent 记忆，含防抖队列与持久化存储。


## 2. 技术栈

### 后端
- Python
- LangGraph / LangChain Agent
- Pydantic（配置与模型校验）
- asyncio（异步执行）
- MCP 集成层（`deer_flow/deer_flow_mcp`）

### 数据与存储
- JSON 文件：记忆与扩展配置（`memory.json`、`extensions_config.json`）
- SQLite：LangGraph checkpointer（由 `checkpointer` 配置决定）
- 本地文件系统：线程工作区与输出文档

### 开发与运维工具
- uv（依赖与环境管理）
- pytest（测试）
- ruff（lint/format）
- Makefile（常用任务命令）
- Node.js + npx（启动 `leetcode-mcp-server`）
- TypeScript（`leetcode-mcp-server` 子项目）


## 3. 项目架构

### 3.1 整体架构

项目由四层组成：
1. 运行入口层：`main.py` 负责启动 agent、读取用户输入、输出结果、记录日志。
2. 编排与执行层：`deer_flow/agents/lead_agent` 负责模型、工具与中间件编排。
3. 工具与数据接入层：`deer_flow/tools` + `deer_flow/sandbox` + `deer_flow/deer_flow_mcp`。
4. 持久化与配置层：`deer_flow/config` + `.deer_flow/*`（记忆、线程目录、自定义 agent）。

### 3.2 模块关系

- `main.py` 调用 `make_lead_agent` 构建主智能体。
- `lead_agent/agent.py` 根据 `config.yaml` 与 agent 配置加载模型、工具、中间件。
- `tools/tools.py` 组合三类工具：
  - 配置化工具（web/file/bash）
  - 内置工具（澄清、文件呈现、任务分解）
  - MCP 工具（从 `extensions_config.json` 动态加载）
- `sandbox` 提供统一文件/命令执行抽象，支持本地 provider 与路径映射。
- `agents/memory` 负责对话记忆提取、队列防抖、文件持久化。

### 3.3 关键数据流向

1. 用户在 CLI 输入请求。
2. `main.py` 组装 `state/messages` 调用 `agent.ainvoke(...)`。
3. 主 agent 根据系统提示和工具可用性决定：直接回答、调用本地工具、或调用 MCP 工具。
4. 工具执行结果回流到 agent，agent 生成最终回复。
5. 若有文档产出，文件写入线程目录的 `outputs`，并通过 `present_files` 暴露给客户端。
6. 会话消息进入 memory queue，防抖后更新 `memory.json`（全局或按 agent）。


## 4. 目录结构

```text
project/
├─ main.py                                  # 项目 CLI 入口，负责会话循环与日志初始化
├─ config.yaml                              # 主配置（模型、工具、sandbox、memory、checkpointer）
├─ extensions_config.json                   # MCP 扩展配置（leetcode/github 等）
├─ pyproject.toml                           # Python 项目定义与依赖入口
├─ Makefile                                 # install/dev/test/lint/format 命令
├─ README.md
├─ deer_flow/
│  ├─ agents/
│  │  ├─ lead_agent/
│  │  │  ├─ agent.py                        # 主 agent 构建与中间件编排
│  │  │  └─ prompt.py                       # 系统提示模板与技能/工具注入
│  │  ├─ middlewares/                       # 澄清、循环检测、memory、thread data 等中间件
│  │  ├─ memory/
│  │  │  ├─ queue.py                        # 记忆更新防抖队列
│  │  │  ├─ updater.py                      # 记忆提取与更新流程
│  │  │  └─ storage.py                      # 记忆文件存储（全局/按 agent）
│  │  └─ thread_state.py                    # Agent 运行态数据模型（sandbox/thread_data/artifacts）
│  ├─ tools/
│  │  ├─ tools.py                           # 工具注册中心（配置工具 + 内置工具 + MCP 工具）
│  │  └─ builtins/
│  │     ├─ present_file_tool.py            # 输出文件暴露接口
│  │     ├─ clarification_tool.py           # 缺失信息澄清接口
│  │     ├─ task_tool.py                    # 子任务/子代理调用接口
│  │     └─ tool_search.py                  # 延迟工具发现接口
│  ├─ sandbox/
│  │  ├─ tools.py                           # 文件/命令工具实现（bash/ls/read/write/replace 等）
│  │  ├─ middleware.py                      # sandbox 生命周期中间件
│  │  ├─ security.py                        # host bash 安全开关策略
│  │  └─ local/
│  │     ├─ local_sandbox.py                # 本地执行与路径映射核心实现
│  │     └─ local_sandbox_provider.py       # LocalSandboxProvider
│  ├─ deer_flow_mcp/
│  │  ├─ client.py                          # MCP server 参数构建
│  │  ├─ tools.py                           # MCP 工具加载与同步包装
│  │  └─ cache.py                           # MCP 工具缓存与热重载
│  ├─ config/
│  │  ├─ app_config.py                      # 主配置模型与加载入口
│  │  ├─ extensions_config.py               # MCP/skills 扩展配置模型
│  │  ├─ paths.py                           # 虚拟路径与本机路径解析
│  │  ├─ agents_config.py                   # 自定义 agent 配置加载
│  │  └─ checkpointer_config.py             # memory/sqlite/postgres 检查点配置
│  ├─ models/
│  │  └─ factory.py                         # 模型工厂（按配置创建 ChatModel）
│  └─ reflection/
│     └─ resolvers.py                       # 类/变量动态解析加载器
├─ .deer_flow/
│  ├─ agents/
│  │  └─ leetcode-assis/
│  │     ├─ config.yaml                     # LeetCode 专用 agent 配置
│  │     ├─ SOUL.md                         # 输出格式与行为约束
│  │     └─ memory.json                     # agent 私有记忆
│  ├─ memory.json                           # 全局记忆
│  └─ threads/
│     └─ <thread-id>/
│        └─ user-data/
│           ├─ workspace/                   # 临时工作目录
│           ├─ uploads/                     # 用户输入文件
│           └─ outputs/                     # 结果文档输出目录
├─ leetcode-mcp-server/                     # TypeScript MCP 子项目（LeetCode 数据服务）
│  ├─ src/index.ts                          # MCP server 入口
│  ├─ src/mcp/tools/                        # user/problem/solution/submission/contest/note 工具
│  └─ src/leetcode/                         # LeetCode CN/Global 服务实现
└─ skills/public/                           # 可选技能库（按需启用）
```


## 5. 核心文件说明

### 5.1 项目入口文件和配置文件

- `main.py`
  - 初始化日志（控制台 + 按天日志文件）
  - 构造运行配置（线程、模型、agent_name、工具开关）
  - 驱动交互循环并调用 `agent.ainvoke`
  - 将会话推送到 memory queue

- `config.yaml`
  - 定义模型、工具组、工具实现路径、sandbox 策略、memory/checkpointer 配置
  - `sandbox.allow_host_bash` 控制是否允许本机 bash 执行

- `extensions_config.json`
  - 定义 MCP servers（如 leetcode/github）
  - 支持 `stdio/sse/http` 连接方式与环境变量注入

- `.deer_flow/agents/leetcode-assis/config.yaml`
  - 定义 LeetCode 专用 agent 的模型与可用工具组

- `.deer_flow/agents/leetcode-assis/SOUL.md`
  - 定义该 agent 的输出合同（总结文档固定结构）与行为策略

### 5.2 核心业务逻辑实现

- `deer_flow/agents/lead_agent/agent.py`
  - 主 agent 构建中心
  - 负责模型选择、中间件链、工具集装配

- `deer_flow/agents/lead_agent/prompt.py`
  - 系统提示模板拼装
  - 注入技能、延迟工具清单、子代理策略、澄清规则

- `deer_flow/tools/tools.py`
  - 工具注册总入口
  - 合并配置化工具、内置工具与 MCP 工具
  - 支持工具延迟发现（tool_search）

- `deer_flow/sandbox/tools.py`
  - 实现 `bash/ls/read_file/write_file/str_replace/glob/grep` 等工具
  - 负责路径校验、路径映射、输出截断、错误脱敏

### 5.3 数据模型和 API 接口

- `deer_flow/agents/thread_state.py`
  - 定义运行态模型 `ThreadState`（sandbox/thread_data/artifacts/todos/uploaded_files 等）

- `deer_flow/config/app_config.py`
  - 定义主配置数据模型 `AppConfig`
  - 暴露配置加载与热重载入口 `get_app_config()/reload_app_config()`

- `deer_flow/config/extensions_config.py`
  - 定义 MCP 配置模型 `ExtensionsConfig` 与 `McpServerConfig`

- `deer_flow/tools/builtins/*.py`
  - 以 LangChain Tool 形式暴露内部能力（可视为项目内部 API）

- `leetcode-mcp-server/src/mcp/tools/*.ts`
  - 对 LeetCode 数据域提供 MCP 接口（user/problem/solution/submission/contest/note）

### 5.4 关键组件和服务模块

- `deer_flow/deer_flow_mcp/tools.py` + `cache.py`
  - MCP 工具加载、同步包装与缓存失效重载

- `deer_flow/sandbox/local/local_sandbox.py`
  - 本地 provider 核心：虚拟路径与本机路径双向映射、命令执行、文件访问

- `deer_flow/agents/memory/queue.py` + `storage.py`
  - 记忆更新防抖队列与文件持久化
  - 支持全局记忆与按 agent 记忆

- `deer_flow/models/factory.py`
  - 统一创建聊天模型，处理 thinking 模式与 tracing 回调注入



