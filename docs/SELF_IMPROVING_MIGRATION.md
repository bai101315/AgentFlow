# AgentFlow Self-Improving 机制迁移设计

> 状态：Phase 0–4 完成，Phase 5 大部分完成（仅剩 Timer 清理），Phase 6 大部分完成
>
> 目标：将 Hermes Agent 的 self-improving 机制完整迁移并收敛到 AgentFlow 的 LangGraph 架构中。
>
> 适用范围：技能库、长期记忆、后台审阅、Curator 生命周期治理、会话隔离、安全与可观测性。

## 1. 文档目的

本文档不是 Hermes 源码说明，而是 AgentFlow 后续实现的工程契约。后续代码、测试和配置变更应以本文档为准；如果现有实现与本文档冲突，应先更新本文档或明确记录偏差。

迁移后的目标是：

```text
用户请求
  -> AgentFlow 前台 Agent 执行
  -> 返回用户响应
  -> 后台提取可复用经验
       -> Memory：保存用户/环境/偏好事实
       -> Skill：保存任务流程、技术方法、坑和验证步骤
  -> Curator：长期维护技能的状态和结构
```

这里的“自我改进”指外部知识库的受控演化，不包括：

- 训练或修改模型参数；
- 修改 AgentFlow 源代码；
- 让后台任务获得完整的终端、网络或浏览器权限；
- 把后台审阅提示词写入用户的真实会话历史。

## 2. 现状基线

AgentFlow 已具备以下基础模块：

| 能力 | 当前实现 | 结论 |
|---|---|---|
| Skill 文件加载 | `backend/skill/loader.py` | 已有 public/custom 两级目录 |
| Skill 写入 | `backend/tools/skill_manage_tool.py` | 前台支持 create/patch/edit/write_file/remove_file；直接 delete 当前统一禁用 |
| Skill 校验 | `backend/skill/validation.py`、`manager.py` | 已有 frontmatter、名称和支持文件路径校验 |
| Skill 历史 | `backend/skill/manager.py` | 已有 `HISTORY.jsonl` |
| Skill 安全扫描 | `backend/skill/security_scanner.py` | 已接入写入链路 |
| Skill usage | `backend/skill/usage.py` | 已有 view/use/write/provenance 数据结构，但读路径接线必须持续验证 |
| Background Review | `backend/agents/middlewares/background_review_middleware.py` | 已有异步审阅和 JSON action 执行 |
| Memory | `backend/agents/middlewares/memory_middleware.py`、`agents/memory/` | 已有过滤、纠正信号、队列、LLM 更新和文件存储 |
| Curator | `backend/skill/curator.py`、`curator_middleware.py` | 代码保留但当前关闭；archive/backup/restore/consolidation 不在本轮范围 |
| 配置 | `config.yaml`、`backend/config/self_improvement_config.py` | 已有配置，但默认值与安全策略需统一 |

已解决的差距（保留历史记录）：

1. ~~Hermes 的 Background Review 是隔离 fork Agent~~ → 已实现独立 `review_agent/runtime.py`，工具集通过 `build_background_skill_manage_tool` 限制为 skill_manage/skills_list/skill_view，不注册普通业务工具。
2. ~~`Skill` 使用统计必须接到真实路径~~ → `skill_view_tool` 记录 view；`sandbox/tools.py` 的 `read_file` 路径记录 use；`skill_manage_tool` 写入时更新 usage。
3. ~~`_default_record()` 默认 `created_by=”agent”`~~ → 已改为 `created_by=None`，仅在 create action 时由 origin 决定；`managed_by_curator` 仅对自动来源为 True。
4. ~~`interval_hours: 0` 等危险组合~~ → `CuratorConfig` 已设 `ge=1`，`model_validator` 强制 `archive_after_days > stale_after_days`。
5. ~~`asyncio.run()` 只能在线程中使用~~ → `SubagentExecutor` 已处理 running loop 检测，隔离到新线程执行。
6. ~~后台状态无清理策略~~ → `_MAX_SEEN_TOOL_CALLS_PER_THREAD=2000`、`_THREAD_STATE_TTL_SECONDS=24h`，在 `after_agent` 时被动清理过期 thread state。

当前剩余改进方向：

1. 线程状态清理目前是被动的（仅在 after_agent 触发），长时间无新请求时不会主动 evict。
2. 事件系统缺少 schema version 字段和 SQLite migration 机制。
3. Curator dry-run/preview 能力未实现，是上线 Phase D 的前提。
4. Memory 内容分类（声明性 vs 程序性）完全依赖 prompt，无确定性后验校验。

## 3. 设计原则

### 3.1 Memory 与 Skill 严格分工

Memory 保存声明性知识：

- 用户身份和稳定偏好；
- 项目环境和工具链事实；
- 用户明确要求长期遵守的行为约束。

Skill 保存程序性知识：

- 一类任务的触发条件；
- 可复用的步骤、命令和 API 调用方式；
- 调试路径、兼容性处理和 Pitfalls；
- 验证方式和完成标准。

判断标准：

```text
“谁/什么/偏好什么” -> Memory
“以后遇到这类任务怎么做” -> Skill
```

### 3.2 前台响应优先

后台 review 和 Memory 更新必须在用户响应已经生成后启动，不得增加主 Agent 的工具循环，不得改变主 Agent 当前 turn 的结果。

### 3.3 后台写入必须可追溯

所有后台写入必须记录：

- `origin`：`foreground`、`background_review`、`curator`、`migration`；
- `thread_id`；
- `parent_thread_id`（如有）；
- `agent_name`；
- `action`；
- `timestamp`；
- 安全扫描结果；
- 变更前后内容或内容摘要。

### 3.4 失败隔离

以下失败均不得影响用户前台响应：

- Review 模型失败；
- action JSON 解析失败；
- Memory LLM 更新失败；
- Curator 任务失败；
- usage/history 元数据写入失败；
- 技能提示词缓存刷新失败。

失败必须记录日志和可观测事件，并允许下一次任务重试。

### 3.5 可恢复优先

自动治理的最大破坏性动作是 archive，不允许自动永久删除。Archive 必须保留：

- 原技能目录；
- archive 时间；
- 原名称；
- 触发原因；
- 可恢复路径；
- 必要时的压缩备份。

## 4. 总体架构

```text
                              +----------------------+
                              |  AgentFlow Foreground |
                              |  LangGraph Agent      |
                              +----------+-----------+
                                         |
                 +-----------------------+------------------------+
                 |                        |                        |
          before_agent              agent execution          after_agent
                 |                        |                        |
        Memory recall/inject       skill_view/use signals   queue memory update
                 |                        |                 count review trigger
                 |                        |                        |
                 +------------------------+------------------------+
                                          |
                                  Background Scheduler
                                          |
                  +-------------------+-------------------+
                  |                                       |
           Background Review                         Curator
                  |                                       |
      propose/apply skill actions             lifecycle/consolidation
                  |                                       |
       +----------+----------+                 +----------+----------+
       |                     |                 |                     |
  SkillStore             MemoryStore      UsageStore             Archive
```

建议将持久化边界明确为：

```text
skills/public/       用户/项目提供的只读技能
skills/custom/       AgentFlow 可维护的自定义技能
skills/custom/.history/
skills/custom/.archive/
skills/custom/.usage.json
.agentflow/memory.json
.agentflow/agents/<agent>/memory.json
.agentflow/self_improvement/
    review_state.json
    curator_state.json
    events.jsonl
    backups/
```

路径必须通过现有 `AGENTFLOW_HOME`/配置路径解析，不得在业务代码中硬编码当前工作目录。

## 5. 一次 Turn 的完整生命周期

### 5.1 Agent 初始化

1. 读取应用配置。
2. 加载 public/custom Skill 元数据索引。
3. 构建 frozen system prompt 和技能索引。
4. 初始化 Memory 队列和存储。
5. 初始化后台 review 状态管理器。
6. 初始化 Curator scheduler，但不在首次观察时立即执行破坏性动作。

### 5.2 Turn 开始

1. 取得 `thread_id`、`agent_name` 和当前运行上下文。
2. 从用户输入中移除上传文件等临时块。
3. 调用 Memory recall，失败时返回空上下文。
4. 将 recalled memory 放在明确的系统上下文边界中，防止记忆内容伪装成新用户指令。
5. 不因 Memory 更新或 Skill 刷新改变当前 turn 的 system prompt 前缀。

### 5.3 Agent 执行

1. 正常执行 LangGraph Agent 和工具循环。
2. `skill_view` 成功后记录 `view_count`。
3. 技能被显式加载到 prompt 或被 `/skill` 指令使用时记录 `use_count`。
4. 过滤重复 tool call，使用稳定 call id；没有 id 时使用 thread、消息序号、工具名和参数哈希。
5. Skill 写入只通过统一的 `skill_manage` 服务完成。

### 5.4 Turn 结束

1. 先返回最终响应。
2. 将用户输入和最终响应加入 Memory debounce queue。
3. 统计本 turn 新增 tool calls；达到阈值时设置 review trigger。
4. 复制只读的消息快照。
5. 后台 review 使用消息快照，不读取会继续变化的前台 state 对象。
6. 更新 Curator scheduler 的最近活动时间。

### 5.5 后台执行

后台任务不得重新进入用户前台图。完成后：

- 写入 self-improvement event；
- 根据通知配置决定是否发送简短摘要；
- 清理 thread-local 状态；
- 不修改父 Agent 的 messages/checkpoint；
- 不结束或轮换父 Agent session。

## 6. Background Review 设计

### 6.1 两阶段职责

Background Review 分成“分析”和“应用”两个阶段：

```text
conversation snapshot
  -> reviewer model
  -> validated actions
  -> policy check
  -> skill_manage service
  -> event + notification
```

当前 AgentFlow 的 `background_review_middleware.py` 已使用此思想。实现时应保持 action 解析器统一使用 `backend/skill/action_parsing.py`，不能在 Background Review 和 Curator 中各写一套脆弱 JSON 解析。

### 6.2 推荐的 action 契约

```json
{
  "actions": [
    {
      "action": "patch_skill",
      "name": "python-debugging",
      "find": "old exact text",
      "replace": "new text",
      "expected_count": 1,
      "reason": "用户纠正了调试顺序"
    },
    {
      "action": "write_support_file",
      "name": "python-debugging",
      "path": "references/provider-quirks.md",
      "content": "...",
      "reason": "形成了可复用的 provider 兼容性经验"
    }
  ]
}
```

允许的 Background Review action：

- `patch_skill`；
- `write_support_file`；
- `create_skill`；
- `noop`。

Background Review 默认禁止：

- `delete`；
- 直接操作任意文件；
- 修改 public skill；
- 修改 AgentFlow 源代码；
- 调用 bash、网络、浏览器和其它业务工具。

### 6.3 Review Prompt 规则

Prompt 必须要求：

1. 优先 patch 当前已加载且相关的 custom skill。
2. 其次 patch 已存在的 class-level umbrella skill。
3. 再考虑添加 `references/`、`templates/` 或 `scripts/`。
4. 只有没有任何合适技能时才 create 新技能。
5. 技能名称必须是 class-level、hyphen-case，不能是 issue、PR、错误字符串或当天任务名称。
6. 一次 review 最多应用 `max_actions_per_review` 个 action。
7. 没有稳定、可复用信号时必须 noop。
8. 用户偏好若属于某类任务的执行方式，应同步进入对应 Skill；用户身份和长期事实才进入 Memory。

### 6.4 隔离方案

短期沿用 AgentFlow 的“受限 action reviewer”模式，但必须实现以下隔离字段：

```python
review_context = {
    "origin": "background_review",
    "execution_context": "background_review",
    "parent_thread_id": parent_thread_id,
    "review_thread_id": review_thread_id,
    "persist_conversation": False,
    "allow_tools": {"skill_manage", "skills_list", "skill_view"},
}
```

长期推荐升级为独立 review runtime：

- review Agent 使用独立 runtime/session id；
- review messages 不进入父 checkpoint；
- review runtime 只能访问 memory/skill service；
- review runtime 不注册普通业务工具；
- review runtime 的所有写入携带 origin；
- review runtime 结束时只回传 action summary，不回传完整内部消息。

禁止通过共享可变 `AgentState` 传递 review 上下文。需要传递的内容必须是不可变消息快照或显式的 `ReviewRequest` 数据类。

### 6.5 Review 并发控制

同一 `thread_id` 同时只允许一个 review：

```text
review_running = true -> 新触发直接合并/丢弃
review 完成或失败 -> finally 清理 review_running
```

必须设置：

- 最大 review 执行时间；
- 最大消息数；
- 最大 action 数；
- 每个技能的写锁；
- 进程退出时的有界等待。

## 7. Skill Store 与写入契约

### 7.1 统一写入链路

所有写入必须通过以下顺序：

```text
校验 action
  -> 校验 name/path
  -> 读取旧内容
  -> 生成新内容
  -> frontmatter 校验
  -> 安全扫描
  -> 原子写入
  -> HISTORY.jsonl
  -> usage/provenance
  -> 清理技能 prompt cache
  -> 写 self-improvement event
```

当前主要入口：

```text
backend/tools/skill_manage_tool.py:_skill_manage_impl
```

### 7.2 Ownership 与 provenance

建议将 usage record 统一为：

```json
{
  "name": "python-debugging",
  "created_at": "2026-08-13T00:00:00+00:00",
  "created_by": "background_review",
  "origins": ["background_review"],
  "last_origin": "background_review",
  "last_activity_at": "2026-08-13T00:00:00+00:00",
  "patch_count": 0,
  "use_count": 0,
  "view_count": 0,
  "state": "active",
  "pinned": false,
  "managed_by_curator": true
}
```

取值约定：

- `foreground_user`：用户明确要求前台创建/维护；默认不进入自动 Curator。
- `background_review`：后台学习创建/修改；可进入 Curator。
- `curator`：Curator 执行的维护动作；不可改变原始作者事实。
- `migration`：迁移脚本产生；是否纳入 Curator 必须显式指定。

`created_by` 是历史事实，不能因为后续 patch 被覆盖。`last_origin` 只表示最近一次写入来源。

当前 `backend/skill/usage.py` 的 `_default_record()` 不应无条件写入 `created_by="agent"`，应按首次写入 origin 决定。

### 7.3 前台与后台权限

前台 `skill_manage`：

- 可在用户明确授权下修改 custom skill；
- 不可修改 public skill；
- 删除前应遵循项目当前的确认策略；
- 所有写入保留历史。

后台 review：

- 只能修改 curator-managed custom skill；
- 不得删除；
- 不得修改 pinned skill；
- 修改前必须先读取目标技能或由宿主提供已校验快照；
- 不能将 foreground 用户技能自动纳入 Curator。

## 8. Memory 设计

### 8.1 当前链路

```text
MemoryMiddleware.after_agent
  -> _filter_messages_for_memory
  -> detect_correction / detect_reinforcement
  -> MemoryQueue.add
  -> debounce Timer
  -> MemoryUpdater.update_memory
  -> FileMemoryStorage.save
```

当前实现已正确过滤：

- tool 消息；
- 带 tool calls 的中间 AI 消息；
- 上传文件临时块。

必须继续保证 Memory 不保存：

- 临时文件路径；
- 一次性任务状态；
- secrets/token/API key；
- review harness prompt；
- 未经用户确认的推测性事实。

### 8.2 Memory 写入来源

Memory 写入需要带：

```text
thread_id
agent_name
origin
signal_type: correction/reinforcement/explicit/summary
confidence
updated_at
```

纠正信号只能提高相关事实的更新优先级，不能自动把任何带有“错误/重试”的句子当成长期偏好。

### 8.3 退出与 flush

队列使用 daemon Timer 时，必须提供显式 flush：

- CLI `/exit`；
- Ctrl-C；
- session reset/new；
- gateway session expiry；
- 测试 fixture teardown。

flush 必须有超时，不能让坏掉的 LLM provider 阻塞进程退出。

## 9. Curator 设计

### 9.1 两类任务

确定性任务：

- 读取 usage；
- 判断是否 active/stale；
- 归档长期未使用技能；
- 写入状态和事件。

LLM consolidation：

- 发现重复或重叠技能；
- 选择 umbrella skill；
- 迁移有价值内容；
- 归档旧技能。

确定性任务默认可开启；LLM consolidation 默认关闭。

### 9.2 状态机

```text
active --超过 stale_after_days 未活动--> stale
stale  --超过 archive_after_days 未活动--> archived
stale  --再次 view/use/write----------------> active
archived --用户显式 restore-----------------> active
```

Pinned 技能跳过所有自动状态转换和自动 consolidation。

### 9.3 调度安全

禁止以下配置组合：

```yaml
interval_hours: 0
min_idle_hours: 0
stale_after_days: 1
archive_after_days: 1
```

推荐校验：

- `interval_hours >= 1`；
- `min_idle_hours >= 0`；
- `archive_after_days > stale_after_days`；
- 首次观察只初始化 `last_run_at`，不立即运行；
- scheduler 运行中拒绝重复运行；
- `Timer` 在完成后从 scheduler 清除；
- 应用关闭时取消 Timer。

当前 `CuratorConfig.interval_hours` 的 `ge=0` 应改为 `ge=1`，并在 `model_validator` 中保证 archive 周期大于 stale 周期。

### 9.4 Archive 备份

每次 Curator run 前创建备份，至少包含：

- custom skill 文件；
- `.usage.json`；
- `.curator_state.json`；
- 运行配置摘要。

备份失败时，Curator 不得执行 archive/consolidation。

## 10. Prompt Cache 与上下文一致性

Skill 写入后必须清理或刷新技能系统提示词缓存，但不能修改正在执行的前台 turn 的历史消息。

后台 review 若升级为 fork Agent：

- 同模型路径可以复用已构建的 system prompt；
- 工具集合必须保持一致或使用独立 cache key；
- 不得在 review 运行中刷新 MCP/tool schema；
- 不得触发 context compression；
- 不得让 review fork 轮换父 session。

如果 AgentFlow 的 prompt cache middleware 不能保证上述条件，则优先使用独立 review prompt，不要错误复用父缓存。

## 11. 配置建议

建议将配置调整为：

```yaml
skill_evolution:
  enabled: true
  auto_create: true
  moderation_model_name: null
  require_background_provenance: true

background_review:
  enabled: true
  skill_nudge_interval: 2
  max_messages: 24
  review_model_name: null
  notifications: "off"
  max_actions_per_review: 3
  timeout_seconds: 120
  max_concurrent_reviews: 1

curator:
  enabled: true
  interval_hours: 168
  min_idle_hours: 2
  stale_after_days: 30
  archive_after_days: 90
  consolidate: true
  model_name: null
```

`curator.enabled` 和 `curator.consolidate` 已开启。Archive 前自动创建 tar.gz 备份；备份失败阻止 archive。Restore 通过前台 `skill_manage action=restore` 触发。

## 12. 分阶段实施计划

### Phase 0：契约与基线

- [x] 固化本文档和配置 schema。
- [x] 运行现有测试并记录基线。
- [x] 确认 `AGENTFLOW_HOME`、skills root、memory storage 的绝对路径解析。
- [x] 为后台任务统一定义 `origin`、`execution_context`、`thread_id`。

验收：配置可解析，所有现有测试基线可复现。✅ 112 tests passing。

### Phase 1：Skill 写入与 provenance

- [x] 修复 usage 默认记录，不再无条件标记 `created_by=agent`。
- [x] 区分 foreground_user 和 background_review。
- [x] 确保 create/patch/write/remove 全部经过同一写入服务；delete 当前统一禁用。
- [x] 写入后生成 event，刷新 skill prompt cache。
- [x] 增加并发写锁、原子写、历史写入失败处理。

验收：✅ 前台技能不会被自动 Curator 管理；后台技能可以被 Curator 识别；失败不会产生半写入状态。

### Phase 2：Skill progressive disclosure 与 usage 接线

- [x] `skills_list` 只返回 metadata。
- [x] `skill_view` 成功后记录 view。
- [x] 技能真正注入 prompt/显式调用后记录 use（通过 sandbox read_file 路径）。
- [x] 排除 `.usage.json`、`.history`、`.archive` 等簿记路径。
- [ ] 增加路径判定的宿主路径和容器路径测试。

验收：view/use 计数真实增长，Curator 根据真实活动而非创建时间判断。

### Phase 3：Background Review 稳定化

- [x] 统一 action JSON 容错解析。
- [x] 增加每 thread review_running 去重。
- [x] 增加 timeout、max actions、max messages。
- [x] 通过受限 service 执行动作，不暴露普通业务工具。
- [x] 所有写入带 background provenance。
- [x] review 失败不影响前台响应。
- [x] 升级到独立 review runtime（`review_agent/runtime.py`）。

验收：✅ 后台 review 能够创建/patch/support file，父 checkpoint 不出现 review prompt 和 review response。

### Phase 4：Memory 完整闭环

- [x] 检查 queue、updater、storage 的错误传播和日志。
- [x] 接入退出时 discard pending；不在退出路径同步调用 Memory 模型。
- [x] 增加 memory write provenance。
- [x] 防止 review prompt、上传路径和工具中间结果进入 Memory。
- [x] 为 correction/reinforcement/explicit 信号补充测试。

验收：✅ 多轮对话、退出、reset、异常 provider 场景下 memory 不丢失、不污染。

### Phase 5：Curator 生命周期（延期）

- [x] 修复 interval=0 和 archive/stale 周期关系；Curator 仍保持关闭。
- [x] 增加首次运行 defer（`should_run_curator` 首次只写时间戳，返回 False）。
- [x] 实现 pinned 保护和 managed-by-curator 判断。
- [x] 默认关闭 consolidation，手动/配置显式开启。
- [x] 实现 run 前备份（tar.gz 压缩备份到 `.archive/.backups/`）。
- [x] 实现 archive（备份失败阻止 archive）和 restore（名称冲突策略 + history）。
- [x] 启用 Curator 默认配置（`enabled=true`, `consolidate=true`）。
- [ ] 清理 Timer、状态集合和并发任务。

验收：archive、backup、restore 和 consolidation 已实现并通过 18 项专项测试。

### Phase 6：可观测性和运营

- [x] 记录 `review_started/review_completed/review_failed`。
- [x] 记录 `skill_created/skill_patched/skill_archived/skill_restored`。
- [x] 记录 memory update 成功/失败、耗时、模型名、token 使用量（不记录 secrets）。
- [x] 在现有 observability 看板展示 review 和 curator trace。
- [ ] 增加后台任务状态查询能力。

验收：能够从 thread、skill、event 反查一次自动变更的来源和结果。

## 13. 测试矩阵

### 13.1 Action parser

- 纯 JSON object；
- 纯 JSON array；
- ```json fenced JSON；
- JSON 前后夹杂 prose；
- prose 中先出现错误 object、后出现正确 array；
- 括号出现在字符串中；
- 截断 JSON；
- 垃圾输入；
- 解析失败返回空 action，不抛出后台线程异常。

### 13.2 Skill 写入

- 合法 create；
- frontmatter 不合法；
- name/path traversal；
- support file 逃逸；
- executable script 扫描拒绝；
- patch expected_count 不匹配；
- 并发 patch；
- foreground/background provenance；
- public skill 不可直接修改；
- archive 后 restore；
- history 和 usage 原子性/失败行为。

### 13.3 Background Review

- 未达到阈值不触发；
- 达到阈值只触发一次；
- skill_manage 后计数重置；
- 同 thread review 去重；
- review 模型异常；
- action apply 部分成功；
- review 不写 checkpoint；
- review 不调用普通工具；
- review 不污染 Memory；
- review 完成后清理状态。

### 13.4 Memory

- 只保留 human + final AI；
- 过滤 tool call、中间 AI 和上传块；
- correction/reinforcement 检测；
- debounce；
- explicit flush；
- provider 超时；
- provider 异常不阻塞前台；
- review prompt 不落 Memory；
- 多 agent memory 隔离。

### 13.5 Curator

- 首次运行 defer；
- interval 到期运行；
- idle gate；
- active -> stale；
- stale -> archived；
- 再次使用 stale -> active；
- pinned 不变；
- 非 managed skill 不变；
- archive 可恢复；
- backup 失败不执行破坏动作；
- consolidation 默认不执行；
- Timer 不重复、不泄漏。

### 13.6 运行级验证

至少执行：

```bash
uv run pytest backend/tests -q
make test
make lint
make format
```

如果仓库当前存在与本迁移无关的失败，必须记录基线失败和新增失败，不能把全绿作为未经验证的结论。

## 14. 关键不变量

实现过程中必须保持以下不变量：

1. Background Review 不得修改父 Agent 的 messages/checkpoint/session。
2. Background Review 不得调用普通业务工具。
3. Memory 不得保存 secrets、临时文件路径和 review harness。
4. Skill 写入必须经过校验、安全扫描、原子写入和历史记录。
5. `created_by` 表示首次来源，不能被后续 patch 覆盖。
6. Curator 只处理明确标记为 managed 的技能。
7. Curator 自动动作最多 archive，不得永久删除。
8. Pinned skill 不得被自动 archive 或 consolidation。
9. 后台失败不得影响用户已经收到的前台响应。
10. 所有异步队列、Timer、线程状态都有清理和有界退出路径。
11. Skill prompt cache 刷新不能回写或重排主会话历史。
12. 解析器、路径校验、provenance 和写入服务只能有一个权威实现。

## 15. 研究与实现入口

推荐按以下顺序修改代码：

1. `backend/skill/usage.py`
2. `backend/tools/skill_manage_tool.py`
3. `backend/skill/action_parsing.py`
4. `backend/agents/middlewares/background_review_middleware.py`
5. `backend/agents/middlewares/memory_middleware.py`
6. `backend/agents/memory/queue.py`
7. `backend/agents/memory/updater.py`
8. `backend/skill/curator.py`
9. `backend/agents/middlewares/curator_middleware.py`
10. `backend/agents/lead_agent/agent.py`
11. `config.yaml`
12. `backend/config/self_improvement_config.py`
13. `backend/tests/`

每完成一个阶段，都应先补测试再进入下一阶段。不要同时重构 Skill、Memory、Curator 和 Agent 装配，否则很难判断隔离问题来自哪条链路。

## 16. 完成定义

当以下条件全部满足时，才认为 Hermes self-improving 机制迁移完成：

- 前台和后台写入来源可区分、可追踪；
- Skill/Memory 分工和过滤规则有测试保护；
- Background Review 不污染主会话、不拥有普通工具权限；
- Skill usage 由真实 view/use 路径更新；
- Curator 生命周期可配置、可暂停、可恢复、可回滚；
- 自动归档前有备份，自动动作不永久删除；
- 后台任务有超时、并发控制、失败隔离和 shutdown flush；
- observability 可以查看自动改进事件及其关联 thread/skill；
- `uv run pytest backend/tests -q`、`make test`、`make lint` 和 `make format` 的结果已实际执行并记录。

## 17. 上线计划表与当前实现状态

本节是当前版本的上线边界，以安全优先为原则。代码已经实现的能力不代表默认上线；涉及自动改变、移动、删除或合并长期知识的操作，必须经过单独的灰度和人工确认。

### 17.1 当前版本上线范围

| 模块/功能 | 当前状态 | 上线状态 | 说明 |
|---|---|---|---|
| Skill `skills_list` metadata 列表 | 已实现 | 上线 | 只返回 metadata，不返回 Skill 正文 |
| Skill `skill_view` 查看 | 已实现 | 上线 | 成功查看记录 `view` usage |
| Skill 前台 `patch/edit/write_file` | 已实现 | 上线 | 经过统一校验、安全扫描和原子写入 |
| Skill 前台 `remove_file` | 已实现 | 谨慎上线 | 仅支持文件级修改，保留 history；不允许删除整个 Skill |
| Skill 后台 `create/patch/edit/write_file` | 已实现 | 受限上线 | 仅允许 `background_review` 管理的 Skill，受 action 上限约束 |
| Skill 直接 `delete` | 已禁用 | 暂缓上线 | 前台和后台均禁止，使用 archive 替代 |
| Skill 前台 `archive` | 已实现 | 上线 | 用户手动归档 skill，自动备份后移动到 .archive/ |
| Skill 前台 `restore` | 已实现 | 上线 | 从 .archive/ 恢复 skill，名称冲突时需 force 确认 |
| Skill history/usage/provenance | 已实现 | 上线 | history/usage 失败不回滚已经成功的正文写入 |
| Background Review 隔离 runtime | 已实现 | 受限上线 | 独立 review thread，不注册普通业务工具 |
| Background Review timeout/concurrency/max actions | 已实现 | 受限上线 | 超时保留已经成功写入的内容 |
| Background Review 事件审计 | 已实现 | 上线 | JSONL 与 SQLite 双写，单侧失败不影响前台 |
| Memory 对话过滤 | 已实现 | 上线 | 过滤工具消息、中间 AI 消息和上传文件块 |
| Memory secret 输入/输出拒绝 | 已实现 | 上线 | 命中后整次拒绝，不保存部分结果 |
| Memory debounce queue | 已实现 | 上线 | 退出时清空 pending，不同步调用 Memory 模型 |
| Memory dropped/failed/rejected/completed 事件 | 基本实现 | 受限上线 | 仍需补齐 provenance、耗时和模型/token 字段 |
| Observability SQLite `background_events` | 已实现 | 上线 | 保存结构化事件和脱敏 metadata |
| Self-Improving 看板与只读 API | 已实现 | 内部上线 | 仅展示事件元数据，不展示正文、摘要或 secret |
| Curator scheduler | 已实现且默认启用 | 上线 | 完成备份、恢复、consolidation 全链路，默认 enabled=true |

### 17.2 暂缓上线的危险操作

以下功能即使代码中存在基础实现，也暂时不进入生产自动链路：

| 危险操作 | 暂缓原因 | 重新上线前必须满足 |
|---|---|---|
| 直接删除 Skill | 不可逆，可能同时丢失 `SKILL.md` 和支持文件 | 保留全量备份、二次确认、恢复演练和审计事件；在此之前继续全局禁用 |
| ~~Curator 自动 archive~~ | ✅ 已上线 | 已实现：archive 前自动 tar.gz 备份，备份失败阻止 archive，pinned skill 保护 |
| ~~自动 restore/覆盖恢复~~ | ✅ 已上线 | 已实现：名称冲突策略（默认拒绝/force 覆盖），恢复前备份现有 skill，history 记录 |
| ~~LLM consolidation~~ | ✅ 已上线 | 已实现：默认启用，archive 动作带备份，max_actions=8 限制 |
| ~~Curator 默认自动启用~~ | ✅ 已上线 | 已实现：完整状态机、备份、恢复、pinned 保护、调度安全 |

### 17.3 已完成但仍需完善的功能

| 功能 | 当前已做到 | 后续完善项 |
|---|---|---|
| Skill 写入一致性 | 原子正文写入、history、usage、事件隔离 | 增加 history/usage/cache 各类失败注入测试和写入 diff 查询 |
| Background Review lineage | 已记录 thread、parent thread、review thread、agent、model、耗时 | 增加后台任务状态查询、运行结果保留策略和生产告警 |
| Memory 安全分工 | Prompt 已区分声明性 Memory 与程序性 Skill | 增加确定性 schema/类别校验，阻止 procedure/debugging 内容绕过 Prompt 写入 |
| Memory 失败闭环 | 有 started/completed/failed/rejected/dropped 事件 | 增加 `origin`、`confidence`、模型名、耗时和 token usage |
| Observability 双写 | JSONL 和 SQLite 独立写入 | 增加重试/告警统计、事件保留策略和数据库迁移版本管理 |
| 看板筛选与分页 | 已支持 event type、status、thread、skill 和详情 | 增加权限控制、空库初始化、移动端验收和浏览器自动化测试 |
| Skill usage view/use | 已接入 Skill 查看和部分使用路径 | 完成所有 prompt 注入、显式调用和宿主/容器路径的 usage 测试 |
| 工程质量 | backend 测试和本轮相关 Ruff 已通过 | 清理全仓 Ruff、补齐 `make lint`/`make format` 可重复验收 |

### 17.4 需要新增的功能

在危险操作暂缓期间，优先新增以下只读或可恢复能力：

| 新功能 | 目标 | 上线前验收 |
|---|---|---|
| Curator dry-run | 只计算 stale/archive/consolidation 候选，不修改文件 | 输出候选、原因、影响范围，不产生 Skill 变更 |
| Skill diff/preview | 在人工确认前查看正文和支持文件差异 | 不修改内容，支持按 event/thread/skill 反查 |
| ~~Archive/restore 管理 API~~ | ✅ 已实现 | `skill_manage` action=archive/restore，名称冲突拒绝、备份、history 和事件完整 |
| Backup manifest | 校验每次备份的文件清单和 hash | 备份不完整时禁止后续治理动作 |
| Background task status API | 查询 review、Memory、Curator 的运行状态 | 只读、分页、无正文、状态与事件一致 |
| Memory 管理界面 | 查看、确认和清理 Memory fact | 默认隐藏 secret，删除保留审计且支持回滚策略 |
| 事件 schema/version migration | 防止 JSONL/SQLite 字段随迭代失配 | 老事件可读，新事件字段有版本和兼容测试 |

### 17.5 分阶段上线顺序

| 阶段 | 允许上线内容 | 禁止内容 | 退出条件 |
|---|---|---|---|
| Phase A：只读观测 | 看板、事件查询、Skill metadata/view、usage 统计 | 所有自动 archive、delete、restore、consolidation | 事件双写稳定，敏感信息扫描通过 |
| Phase B：受限写入 | Background Review 的 create/patch/edit/write_file，严格 action 上限 | delete、archive、restore、consolidation | Review 失败隔离、回归测试和人工抽检通过 |
| Phase C：Memory 闭环 | Memory 过滤、secret 拒绝、事件和退出 discard | 退出同步调用模型，自动保存 procedure | Memory provenance、失败事件和数据隔离测试通过 |
| Phase D：Curator 预览 | dry-run、候选列表、备份 manifest、diff 预览 | 自动移动或覆盖文件 | 备份恢复演练和人工审批流程通过 |
| Phase E：可恢复治理 | 经确认的 archive/restore | 永久 delete、无备份治理 | 灰度运行、回滚演练、审计完整 |
| Phase F：高级治理 | 显式配置的 consolidation | 默认自动合并、无 diff 合并 | 人工确认、最大动作数和失败补偿全部通过 |

当前版本已完成 Phase A/B/C/E/F 的全部能力；Phase D（Curator dry-run 预览）待后续补充。

---

## 附录：Hermes 到 AgentFlow 的模块映射

| Hermes | AgentFlow | 迁移要求 |
|---|---|---|
| `tools/skill_manager_tool.py` | `backend/tools/skill_manage_tool.py` | 统一写入、provenance、权限和历史 |
| `tools/skills_tool.py` | `backend/skill/loader.py` + 技能工具 | progressive disclosure、view/use 统计 |
| `tools/skill_usage.py` | `backend/skill/usage.py` | 修正 ownership、接真实访问路径 |
| `agent/background_review.py` | `background_review_middleware.py` | 保留 action 模式，逐步升级 runtime 隔离 |
| `agent/curator.py` | `backend/skill/curator.py` | 加强备份、恢复、调度安全 |
| `tools/memory_tool.py` | `backend/agents/memory/storage.py` | Memory CRUD 和来源记录 |
| `agent/memory_manager.py` | `memory_middleware.py` + `agents/memory/` | recall/sync/flush/失败隔离 |
| `agent/turn_finalizer.py` | LangGraph `after_agent` middleware | 明确触发顺序和前台响应优先 |
