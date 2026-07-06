# AgentFlow Agent 实现参考文档

> 生成日期：2026-07-03  
> 基于代码：`C:\Users\BAI\Desktop\project\backend\agents\`

---

## 1. 目录结构

```
backend/agents/
├── __init__.py
├── thread_state.py          # Agent 状态 Schema 定义
├── checkpointer/
│   ├── __init__.py
│   ├── provider.py          # 同步 checkpointer
│   └── async_provider.py    # 异步 checkpointer
├── lead_agent/
│   ├── __init__.py          # 导出 make_lead_agent
│   ├── agent.py             # Agent 创建 + 中间件装配
│   └── prompt.py            # System Prompt 构建 + 技能缓存
├── memory/
│   ├── __init__.py          # 模块导出
│   ├── storage.py           # 文件存储（memory.json）
│   ├── queue.py             # 防抖队列
│   ├── updater.py           # LLM 驱动记忆更新
│   └── prompt.py            # 记忆更新 Prompt 模板
└── middlewares/
    ├── __init__.py           # （空）
    ├── prompt_cache_middleware.py      # Prompt 冻结缓存
    ├── memory_middleware.py            # 记忆队列触发
    ├── background_review_middleware.py # 后台自改进审查
    ├── session_search_middleware.py    # 会话索引
    ├── clarification_middleware.py     # 澄清拦截
    ├── loop_detection_middleware.py    # 循环检测
    ├── tool_error_handling_middleware.py # 工具错误处理
    ├── todo_middleware.py              # 任务清单
    ├── title_middleware.py             # 会话标题生成
    ├── curator_middleware.py           # 技能生命周期管理
    ├── deferred_tool_filter_middleware.py # 延迟工具过滤
    ├── dangling_tool_call_middleware.py   # 孤立 ToolMessage 修复
    ├── llm_error_handling_middleware.py   # LLM 错误重试
    ├── sandbox_audit_middleware.py        # 沙箱审计
    ├── thread_data_middleware.py          # 线程数据注入
    └── uploads_middleware.py              # 上传文件处理
```

---

## 2. Agent 创建流程

### 2.1 入口

`main.py` → `make_lead_agent(config, checkpointer, custom_middlewares)`  
文件：`backend/agents/lead_agent/agent.py`

### 2.2 执行步骤

1. **解析配置**：从 `RunnableConfig.configurable` 中提取参数
   - `thinking_enabled` / `reasoning_effort`
   - `model_name`（优先级：请求指定 > Agent 配置 > 全局默认）
   - `is_plan_mode` / `subagent_enabled` / `max_concurrent_subagents`
   - `is_bootstrap` / `agent_name`

2. **加载 Agent 配置**：`load_agent_config(agent_name)` → Agent 专属 model/tool_groups/skills

3. **模型解析**：`_resolve_model_name()` → 找 DeepSeek V4（默认）或 fallback 到第一个可用模型

4. **预热技能缓存**：`warm_enabled_skills_cache()` → 后台加载已启用技能列表

5. **创建 Prompt 缓存中间件**：`_make_prompt_cache_middleware()` → 计算签名，生成 `PromptCacheMiddleware`

6. **装配中间件链**：`_build_middlewares()` → 按固定顺序注册所有中间件

7. **创建 LangGraph Agent**：`create_agent(model, tools, middleware, system_prompt=PROMPT_CACHE_PLACEHOLDER, checkpointer, state_schema=ThreadState)`

### 2.3 关键设计决策

- **System Prompt 用占位符**：`PROMPT_CACHE_PLACEHOLDER` = `"Session prompt is injected by PromptCacheMiddleware."`  
  真正的 prompt 由 `PromptCacheMiddleware.wrap_model_call()` 动态注入，实现冻结缓存。

- **Bootstrap 模式**：`is_bootstrap=True` 时，额外添加 `setup_agent` 工具，用于首次创建自定义 Agent 的引导对话。

---

## 3. 中间件架构

### 3.1 注册顺序（`_build_middlewares`）

| 顺序 | 中间件 | 类型 | 职责 |
|---|---|---|---|
| 1 | 运行时中间件（tool_error_handling 等） | `build_lead_runtime_middlewares()` | 基础运行时保障 |
| 2 | `PromptCacheMiddleware` | 自定义 | Prompt 冻结与缓存 |
| 3 | `TodoMiddleware` | 条件（plan_mode） | 任务清单管理 |
| 4 | `TitleMiddleware` | 条件（title_enabled） | 会话标题生成 |
| 5 | `MemoryMiddleware` | 始终 | 记忆队列触发 |
| 6 | `SessionSearchMiddleware` | 条件（session_search.enabled） | 会话索引写入 |
| 7 | `BackgroundReviewMiddleware` | 条件（background_review.enabled） | 后台自改进审查 |
| 8 | `CuratorMiddleware` | 条件（curator.enabled） | 技能生命周期管理 |
| 9 | `DeferredToolFilterMiddleware` | 条件（tool_search.enabled） | 隐藏延迟工具 schema |
| 10 | `LoopDetectionMiddleware` | 始终 | 工具调用循环检测 |
| 11 | `ClarificationMiddleware` | **始终最后** | 澄清请求拦截 |

### 3.2 各中间件详解

#### PromptCacheMiddleware
- **文件**：`middlewares/prompt_cache_middleware.py`
- **钩子**：`before_model`（首次生成缓存）、`wrap_model_call`（每次注入）
- **机制**：
  - 每个 thread 的第一个 system prompt 被"冻结"到 `state["cached_system_prompt"]`
  - 通过 `build_prompt_cache_signature()` 计算 SHA256 签名（含 model_name、agent_name、tool_groups、skills 等）
  - 签名变化时自动重新生成 prompt
  - 每次 LLM 调用后提取 `usage_metadata` 中的 cache 指标并记录
- **关键方法**：`_get_frozen_prompt()`、`_log_cache_usage()`

#### MemoryMiddleware
- **文件**：`middlewares/memory_middleware.py`
- **钩子**：`after_agent`
- **机制**：
  - 每轮对话结束后，过滤消息（去掉 tool_calls 和上传信息块）
  - 检测修正信号（`detect_correction`）和强化信号（`detect_reinforcement`）
  - 放入 `MemoryUpdateQueue` 防抖队列
- **信号检测**：中文 + 英文正则，如"不对"、"你理解错了"、"exactly right"等

#### BackgroundReviewMiddleware
- **文件**：`middlewares/background_review_middleware.py`
- **钩子**：`after_agent`
- **机制**：
  - 追踪每个 thread 的 tool_call 计数，累积到 `skill_nudge_interval`（默认 10）触发后台审查
  - 独立 LLM 调用审查最近对话，输出 JSON actions
  - 支持 4 种行动：`create_skill`、`patch_skill`、`write_support_file`、`noop`
  - 明确的正负信号过滤规则（保留风格纠正、工作流修正，排除一次性任务、环境问题）
  - 在 daemon 线程中异步执行，不阻塞前台

---

## 4. Memory 系统

### 4.1 存储格式

`memory.json` 结构：
```json
{
  "user": {
    "workContext":    { "summary": "...", "updatedAt": "..." },
    "personalContext":{ "summary": "...", "updatedAt": "..." },
    "topOfMind":      { "summary": "...", "updatedAt": "..." }
  },
  "history": {
    "recentMonths":     { "summary": "...", "updatedAt": "..." },
    "earlierContext":   { "summary": "...", "updatedAt": "..." },
    "longTermBackground":{ "summary": "...", "updatedAt": "..." }
  },
  "facts": [
    {
      "id": "fact_xxxxxxxx",
      "content": "...",
      "category": "preference|correction|context|behavior|knowledge",
      "confidence": 0.95,
      "createdAt": "...",
      "source": "thread-id"
    }
  ]
}
```

### 4.2 更新管线

```
MemoryMiddleware.after_agent()
  → 过滤消息（去 tool_calls、上传块）
  → 检测信号（correction/reinforcement）
  → MemoryUpdateQueue.add()
       ↓ (防抖等待 debounce_seconds)
  → MemoryUpdateQueue._process_queue()
       ↓ (遍历队列中的 ConversationContext)
  → MemoryUpdater.update_memory()
       ↓
  → LLM 调用（MEMORY_UPDATE_PROMPT）
       ↓
  → 解析 JSON → _apply_updates()
       ↓
  → 去重事实 → 限制 max_facts → 清理上传引用
       ↓
  → FileMemoryStorage.save() → memory.json
```

### 4.3 关键实现细节

- **防抖**：同一 thread 的多次更新在防抖窗口内合并，旧上下文被替换
- **事实去重**：通过 `content.strip().casefold()` 做 key，避免重复事实
- **置信度阈值**：低于 `fact_confidence_threshold` 的事实不写入
- **事实上限**：超过 `max_facts` 时按置信度排序保留前 N 个
- **上传引用清理**：`_strip_upload_mentions_from_memory()` 正则移除文件上传描述，防止会话级文件路径污染长期记忆

### 4.4 Per-Agent 隔离

`MemoryMiddleware(agent_name=xxx)` → 读写 `{agent_name}/memory.json`，不同 Agent 互不干扰。

---

## 5. Thread State

**文件**：`thread_state.py`

```python
class ThreadState(AgentState):
    sandbox: SandboxState       # { sandbox_id: str }
    thread_data: ThreadDataState # { workspace_path, uploads_path, outputs_path }
    title: str
    artifacts: list[str]        # 带 reducer（自动去重合并）
    todos: list
    uploaded_files: list[dict]
    cached_system_prompt: str   # Prompt 缓存中间件注入
    cached_system_prompt_signature: str
```

`artifacts` 使用 `Annotated[list[str], merge_artifacts]` 实现 reducer 模式——多个中间件并发写入时自动去重合并。

---

## 6. System Prompt 构建

**文件**：`lead_agent/prompt.py`

### 6.1 模板结构

`SYSTEM_PROMPT_TEMPLATE` 包含以下段落（按顺序）：

1. `<role>` — Agent 名字 + 身份声明
2. `<soul>` — SOUL.md 文件内容
3. `<memory>` — 从 memory.json 注入的记忆（条件：`memory_config.injection_enabled`）
4. `<thinking_style>` — 思考规范
5. `<clarification_system>` — 澄清机制
6. `<skill_system>` — 可用技能列表 + 自进化策略
7. `<session_search_system>` — 会话检索说明
8. `<available-deferred-tools>` — 延迟加载工具列表
9. `<subagent_system>` — 子代理编排策略（含并发限制、批量调度）
10. `<working_directory>` — 工作目录说明
11. `<response_style>` — 回复风格
12. `<citations>` — 引用规范
13. `<critical_reminders>` — 关键提醒（含 Orchestrator Mode 提示）

### 6.2 技能 Prompt 缓存

`_get_cached_skills_prompt_section()` 使用 `@lru_cache(maxsize=32)`，按 `(skill_signature, available_skills_key, container_base_path, skill_evolution_section)` 做 key。技能列表变化时清除缓存。

### 6.3 Prompt 构建流程

```
build_session_prompt()
  → apply_prompt_template()
    → _get_memory_context()   # 读取 memory.json
    → _build_subagent_section() # 子代理策略
    → get_skills_prompt_section() # 技能列表（LRU cached）
    → get_deferred_tools_prompt_section() # 延迟工具列表
    → get_session_search_prompt_section()
    → SYSTEM_PROMPT_TEMPLATE.format()
    → 追加 <current_date>
```

---

## 7. 自改进机制汇总

项目中有两层自改进：

### 7.1 前台：skill_manage 工具
- Agent 在对话中直接调用 `skill_manage` 创建/修改技能
- 限制在 `skills/custom/` 目录，有安全扫描 + 历史记录

### 7.2 后台：BackgroundReviewMiddleware
- 自动触发，无需用户干预
- 累积 10 个工具调用 → LLM 审查 → 自动 patch/create 技能
- 精心设计的信号过滤（保留可复用模式，排除一次性任务）

---

## 8. 关键文件速查

| 功能 | 文件路径 |
|---|---|
| Agent 创建 | `lead_agent/agent.py` |
| Prompt 构建 | `lead_agent/prompt.py` |
| Thread State | `thread_state.py` |
| Prompt 缓存 | `middlewares/prompt_cache_middleware.py` |
| 记忆队列 | `memory/queue.py` |
| 记忆更新 | `memory/updater.py` |
| 记忆存储 | `memory/storage.py` |
| 记忆触发 | `middlewares/memory_middleware.py` |
| 自改进审查 | `middlewares/background_review_middleware.py` |
| 技能管理工具 | `tools/skill_manage_tool.py` |
| 会话搜索 | `session_search/store.py` |
