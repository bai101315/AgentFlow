# LeetCode 智能体系统项目文档

## 1. 项目概述
核心能力概览：
- LeetCode 数据接入：通过 MCP server 读取用户状态、题目、提交、题解、竞赛与笔记数据。
- 刷题能力分析：输出做题数量、难度分布、知识点覆盖、活跃趋势、薄弱点与改进建议。
- 总结文档生成：按固定 Markdown 模板生成复盘文档，覆盖题目、代码、关键思路、总体诊断与后续计划。
- 可配置 Agent：支持 `leetcode-assis` 这类专用人格（SOUL）与工具权限组合，而不是单一固定 prompt。
- 本地工具执行：支持 `ls/read_file/write_file/str_replace/bash` 等工具，并对线程工作区、路径映射、宿主机命令权限做隔离控制。
- 线程级工作区：每个 thread 拥有独立 `workspace/uploads/outputs`，会话产物不会彼此串线。
- 分层记忆系统：同时支持全局记忆和按 agent 记忆，并带有防抖、纠错信号、去重和持久化策略。

## 2. 核心创新点

### 2.1 记忆系统：不是“能记住”，而是“不会把系统记坏”

- 独特点：记忆被拆成全局记忆和 agent 私有记忆两层，不同线程的对话更新先进入防抖队列，再统一回写，不会因为连续多轮交互把记忆系统写成噪声池。
- 技术门槛：`backend/agents/memory/queue.py` 做按 thread 合并、防抖和批处理；`backend/agents/memory/storage.py` 做 mtime 感知缓存和临时文件原子写入；`backend/agents/memory/updater.py` 做事实去重、置信度筛选、最大 fact 数裁剪。
- 解决的反直觉问题：长期记忆最危险的不是“记不住”，而是“把临时信息和错误信息记成长期事实”。这里专门识别 correction/reinforcement 信号，把用户纠正过的内容高优先级回写；同时剔除上传文件事件这类会话级信息，避免 Agent 在未来会话里追着一个已经不存在的文件跑。

### 2.2 Windows 沙箱：不是“能跑命令”，而是“在本机上也尽量做出隔离语义”

- 独特点：项目没有偷懒地假设 Linux 环境，而是正面处理 Windows 场景，把 PowerShell、`cmd.exe`、Unix shell fallback、容器路径到本机路径映射纳入同一套抽象。
- 技术门槛：`backend/sandbox/local/local_sandbox.py` 同时实现路径正向解析、输出反向映射、命令中的路径替换、只读挂载识别；`backend/sandbox/security.py` 明确把 `LocalSandboxProvider` 视为非安全边界，因此默认禁用高风险 host bash。
- 解决的反直觉问题：很多所谓“本地沙箱”本质上只是把命令直接交给宿主机执行，路径泄漏、权限越界、Windows 兼容性都靠运气。这里反而先承认本地 provider 不是强隔离容器，因此优先保证可控、可审计、可降权，而不是把“不安全执行”包装成“已经沙箱化”。

### 2.3 Skill 机制：不是“多一个插件目录”，而是把 Agent 自我扩展做成可治理能力

- 独特点：Skill 不是一段 prompt 文本，而是带 frontmatter、支持 `references/templates/scripts/assets` 结构、可启停、可校验、可留历史的能力单元。
- 技术门槛：`backend/skill/manager.py` 对 skill 名称、目录和 supporting file 路径做严格约束，直接阻断路径穿越；`backend/skill/validation.py` 校验 frontmatter；`backend/skill/security_scanner.py` 在写入前做模型级安全扫描，扫描失败时对可执行内容默认阻断。
- 解决的反直觉问题：真正难的不是“Agent 会不会调用 Skill”，而是“你敢不敢让 Agent 生产或修改 Skill”。这个实现处理的是 Skill 自演化最容易变成 prompt injection 和越权入口的问题，让 Skill 从展示型功能变成了可逐步开放的系统能力。

### 2.4 多 Agent：不是“拆个任务”，而是把异步工具链里的并发一致性补齐

- 独特点：子 Agent 不是同步包装的玩具接口，而是完整的后台执行引擎，具备任务状态、消息流式回传、超时、取消、清理和工具权限过滤。
- 技术门槛：`backend/subagents/executor.py` 分离 scheduler pool、execution pool、isolated loop pool，专门处理“父 Agent 已在事件循环中，子 Agent 又要继续调用异步 MCP 工具”这个非常常见、却很少有人认真解决的冲突；`backend/tools/builtins/task_tool.py` 负责后台轮询、状态事件推送、取消后的延迟清理。
- 解决的反直觉问题：多 Agent 最大的问题通常不是“不会拆任务”，而是拆完以后线程挂住、事件循环冲突、超时后还在后台偷跑。这里把实时消息采集、协作式取消、终态清理和超时兜底都补齐了，所以它不是“支持多 Agent”，而是“把多 Agent 做到了能长期稳定跑”。

## 3. 技术栈

### 后端
- Python
- LangGraph / LangChain Agent
- Pydantic（配置与模型校验）
- asyncio（异步执行）
- MCP 集成层（`backend/deer_flow_mcp`）

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

## 4. 项目架构

### 4.1 整体架构

项目由四层组成：
1. 运行入口层：`main.py` 负责启动 agent、读取用户输入、输出结果、记录日志。
2. 编排与执行层：`backend/agents/lead_agent` 负责模型、工具、中间件与 prompt 编排。
3. 工具与数据接入层：`backend/tools` + `backend/sandbox` + `backend/deer_flow_mcp`。
4. 持久化与配置层：`backend/config` + `.deer_flow/*`（记忆、线程目录、自定义 agent）。

### 4.2 模块关系

- `main.py` 调用 `make_lead_agent` 构建主智能体。
- `backend/agents/lead_agent/agent.py` 根据 `config.yaml` 与 agent 配置加载模型、工具、中间件。
- `backend/tools/tools.py` 组合三类工具：
  - 配置化工具（web/file/bash）
  - 内置工具（澄清、文件呈现、任务分解）
  - MCP 工具（从 `extensions_config.json` 动态加载）
- `backend/sandbox` 提供统一文件/命令执行抽象，支持本地 provider 与路径映射。
- `backend/agents/memory` 负责对话记忆提取、队列防抖、文件持久化。

### 4.3 关键数据流向

1. 用户在 CLI 输入请求。
2. `main.py` 组装 `state/messages` 调用 `agent.ainvoke(...)`。
3. 主 agent 根据系统提示和工具可用性决定：直接回答、调用本地工具、或调用 MCP 工具。
4. 工具执行结果回流到 agent，agent 生成最终回复。
5. 若有文档产出，文件写入线程目录的 `outputs`，并通过 `present_files` 暴露给客户端。
6. 会话消息进入 memory queue，防抖后更新 `memory.json`（全局或按 agent）。

## 5. 目录结构

```text
project/
├─ main.py                                  # 项目 CLI 入口，负责会话循环与日志初始化
├─ config.yaml                              # 主配置（模型、工具、sandbox、memory、checkpointer）
├─ extensions_config.json                   # MCP 扩展配置（leetcode/github 等）
├─ pyproject.toml                           # Python 项目定义与依赖入口
├─ Makefile                                 # install/dev/test/lint/format 命令
├─ README.md
├─ backend/
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
│  ├─ skill/
│  │  ├─ manager.py                         # Skill 管理、命名校验、历史记录
│  │  ├─ validation.py                      # Skill frontmatter 校验
│  │  └─ security_scanner.py                # Skill 安全扫描
│  ├─ subagents/
│  │  ├─ executor.py                        # 子 Agent 后台执行引擎
│  │  └─ registry.py                        # 子 Agent 注册与发现
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

## 6. 核心文件说明

### 6.1 项目入口文件和配置文件

- `main.py`
  - 初始化日志
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

### 6.2 核心业务逻辑实现

- `backend/agents/lead_agent/agent.py`
  - 主 agent 构建中心
  - 负责模型选择、中间件链、工具集装配

- `backend/agents/lead_agent/prompt.py`
  - 系统提示模板拼装
  - 注入技能、延迟工具清单、子代理策略、澄清规则

- `backend/tools/tools.py`
  - 工具注册总入口
  - 合并配置化工具、内置工具与 MCP 工具
  - 支持工具延迟发现（tool_search）

- `backend/sandbox/tools.py`
  - 实现 `bash/ls/read_file/write_file/str_replace/glob/grep` 等工具
  - 负责路径校验、路径映射、输出截断、错误脱敏

### 6.3 数据模型和 API 接口

- `backend/agents/thread_state.py`
  - 定义运行态模型 `ThreadState`（sandbox/thread_data/artifacts/todos/uploaded_files 等）

- `backend/config/app_config.py`
  - 定义主配置数据模型 `AppConfig`
  - 暴露配置加载与热重载入口 `get_app_config()/reload_app_config()`

- `backend/config/extensions_config.py`
  - 定义 MCP 配置模型 `ExtensionsConfig` 与 `McpServerConfig`

- `backend/tools/builtins/*.py`
  - 以 LangChain Tool 形式暴露内部能力，可视为项目内部 API

- `leetcode-mcp-server/src/mcp/tools/*.ts`
  - 对 LeetCode 数据域提供 MCP 接口（user/problem/solution/submission/contest/note）

### 6.4 关键组件和服务模块

- `backend/deer_flow_mcp/tools.py` + `cache.py`
  - MCP 工具加载、同步包装与缓存失效重载

- `backend/sandbox/local/local_sandbox.py`
  - 本地 provider 核心：虚拟路径与本机路径双向映射、命令执行、文件访问、只读挂载控制

- `backend/agents/memory/queue.py` + `storage.py`
  - 记忆更新防抖队列与持久化存储
  - 支持全局记忆与按 agent 记忆

- `backend/skill/manager.py` + `security_scanner.py`
  - Skill 的命名治理、路径边界校验、历史记录与安全扫描

- `backend/subagents/executor.py`
  - 子 Agent 的异步执行、状态跟踪、超时取消和事件循环隔离

- `backend/models/factory.py`
  - 统一创建聊天模型，处理 thinking 模式与 tracing 回调注入
