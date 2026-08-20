#  Hermes 记忆系统完整架构

记忆系统不是单一组件，而是一个四层架构，每层有明确的职责边界。

# 层次一：内置记忆存储 — MemoryStore
这是最底层的持久化引擎，实现在 tools/memory_tool.py。

双文件存储：
~/.hermes/memories/
    ├── MEMORY.md    ← Agent的"个人笔记"（环境事实、项目约定、工具经验）
    └── USER.md      ← Agent对用户的认知（偏好、沟通风格、习惯）

**核心机制**：
```
1. 条目分隔符：用 §（section sign，即 \n§\n）分隔每条记录，支持多行条目

2. 字符限制而非Token限制：
    - MEMORY.md 上限 2200 字符（可配置 memory.memory_char_limit）
    - USER.md 上限 1375 字符（可配置 memory.user_char_limit）
    - 用字符数而非token数是因为字符数与模型无关

3. 冻结快照（Frozen Snapshot）模式 — 这是记忆系统最关键的设计：

    会话启动 → load_from_disk() → 捕获快照到 _system_prompt_snapshot
                                    ↓
    所有轮次 → format_for_system_prompt() 永远返回快照（不变）
                                    ↓
    工具写入 → 更新内存中 live state + 写入磁盘
                但快照不变！系统提示词稳定！
    
    为什么这样做：LLM 提供商的 prompt prefix caching（前缀缓存）依赖于系统提示词的字节稳定性。如果每次写入记忆后都改变系统提示词，缓存就会失效，导致每次API调用都要重新处理整个系统提示词（可能数万token），速度和成本都大幅上升。

4. 原子写入：
    - 写临时文件 → fsync → 原子 rename
    - 读操作总是看到完整文件（旧或新，不会看到半截）
    - 加上 fcntl 文件锁保护并发写入

5. 注入攻击扫描：写入内容会被 _scan_memory_content() 检查：
    - 不可见Unicode字符（零宽字符、BOM、方向控制符）
    - 提示注入模式（"ignore previous instructions" 等）
    - 秘密泄露模式（curl/wget 搭配 API KEY / TOKEN / SECRET 等）

```

## 为什么聊天一直没有更新？
**记忆计数器「不跨进程」持久化**

每次执行 hermes 命令启动新会话时，_turns_since_memory 从 0 开始。当调用次数达不到阈值时，就不会进行更新

# 层次二：记忆管理器 — MemoryManager

实现在 agent/memory_manager.py，是记忆系统的"调度中心"。

MemoryManager
    ├── 内置 provider (builtin, 始终存在)
    └── 外部 provider (最多一个，由 memory.provider 配置决定)

**设计约束**：只允许一个外部 provider，防止 tool schema 膨胀和多个后端冲突。

**核心调度方法**：

| 时机 | 方法 | 作用 |
|------|------|------|
| 系统提示词组装 | build_system_prompt() | 收集所有 provider 的 system_prompt_block |
| 每轮开始前 | prefetch_all(query) | 提前召回相关记忆上下文 |
| 每轮结束后 | sync_all(user_msg, assistant_msg) | 把整轮对话同步到 provider |
| 每轮结束后 | queue_prefetch_all(query) | 排队后台预取，为下一轮准备 |
| 工具调用 | handle_tool_call(name, args) | 路由到正确 provider |
| 会话切换 | on_session_switch(new_id) | 通知所有 provider session ID 变化 |
| 会话结束 | on_session_end(messages) | 会话级事实提取 |
| 上下文压缩前 | on_pre_compress(messages) | 在被丢弃前提取洞察 |
| 子代理完成 | on_delegation(task, result) | 父代理观察子代理的工作 |

上下文隔离（StreamingContextScrubber）：

记忆上下文通过  标签包裹注入，有一套状态机在流式输出时实时剥离这些内容，防止记忆内容泄漏到用户可见的输出中。

# 层次三：外部记忆 Provider 插件

实现在 agent/memory_provider.py（ABC 抽象基类）和 plugins/memory/ 下。

可用的外部 Provider：

| Provider | 特点 |
|----------|------|
| Honcho | AI-native 用户建模，跨会话持久化 |
| Mem0 | 记忆层，图数据库后端 |
| Holographic | 全息记忆存储与检索 |
| Supermemory | 向量化记忆 |
| Hindsight | 回顾式记忆提取 |
| RetainDB | 保留式数据库 |
| Byterover | 字节流记忆 |
| OpenViking | 开源维京记忆 |

每个外部 provider 必须实现的核心接口（与内置 provider 相同的 ABC）：
initialize()        → 建立连接、创建资源
is_available()      → 检查是否配置完成
system_prompt_block() → 静态系统提示词文本
prefetch(query)     → 每轮前召回上下文
queue_prefetch()    → 后台预取排队
sync_turn()         → 每轮后持久化
get_tool_schemas()   → 暴露给模型的工具定义
handle_tool_call()   → 处理工具调用
shutdown()          → 清理退出

可选钩子（override 选择加入）：
- on_turn_start() — 每轮开始的运行时上下文
- on_session_end() — 会话级事实提取
- on_session_switch() — 会话ID切换
- on_pre_compress() — 压缩前提取
- on_memory_write() — 镜像内置记忆写入
- on_delegation() — 观察子代理工作

你的实例当前未启用外部 provider（memory.provider: ''）。

# 层次四：系统提示词注入 — 三层拼装

实现在 agent/system_prompt.py，系统提示词被分为三层：

┌─────────────────────────────────────────┐
│  STABLE 层 (稳定，整个会话不变)           │
│  ├── SOUL.md 或 DEFAULT_AGENT_IDENTITY   │
│  ├── 工具使用指导 (MEMORY_GUIDANCE 等)    │
│  ├── Skills 索引                        │
│  ├── 环境提示 (WSL/Termux等)             │
│  └── 平台提示 (CLI/Telegram/Discord等)    │
├─────────────────────────────────────────┤
│  CONTEXT 层 (按工作目录变化)              │
│  ├── system_message (调用者传入)          │
│  └── AGENTS.md / .cursorrules 等上下文文件 │
├─────────────────────────────────────────┤
│  VOLATILE 层 (每次启动变化)               │
│  ├── MEMORY.md 冻结快照 [N%/NNNN chars]  │
│  ├── USER.md 冻结快照 [N%/NNNN chars]    │
│  ├── 外部 provider system_prompt_block   │
│  └── 时间戳 + 模型 + Provider 信息       │
└─────────────────────────────────────────┘

prompt cache 策略：整个系统提示词作为一条消息缓存，stable 和 context 层在 session 内不变，volatile 层虽每次session启动重建但同一session内不变（因为冻结快照）。只有 context compression 后才会 invalidate_system_prompt() 强制重建。

记忆生命周期 — 完整的轮次流程
    
1. 会话启动 (agent_init.py)
   ├── 读取 config.yaml 中 memory.* 配置
   ├── 创建 MemoryStore 实例
   ├── load_from_disk() → 加载 MEMORY.md + USER.md
   └── 捕获冻结快照（永不改变）

2. 每轮开始 (conversation_loop.py)
   ├── _turns_since_memory += 1
   ├── 如果达到 nudge_interval (默认10轮)
   │   └── _should_review_memory = True
   ├── MemoryManager.prefetch_all() → 外部 provider 召回上下文
   └── MemoryManager.on_turn_start() → 通知所有 provider

3. 系统提示词组装 (system_prompt.py)
   └── 使用 format_for_system_prompt() 的冻结快照
       （而不是实时内存状态）

4. 工具调用 "memory" (tool_executor.py)
   ├── MemoryStore.add/replace/remove()
   │   ├── 更新内存中 live state
   │   └── 原子写入磁盘
   └── MemoryManager.on_memory_write()
       （桥接到外部 provider，如果启用）

5. 每轮结束 (run_agent.py)
   ├── MemoryManager.sync_all(user_msg, assistant_msg)
   └── MemoryManager.queue_prefetch_all()
       （后台预取，为下轮准备）

6. 后台回顾 (background_review.py)
   └── 如果 _should_review_memory 或技能触发
       ├── fork 一个子 agent（共享父 agent 的 provider/model/credentials）
       ├── 限制工具仅 memory + skill_manage
       ├── 发送 MEMORY_REVIEW_PROMPT（分析对话，决定是否写入记忆）
       └── 子 agent 通过 memory 工具写入 → 持久化到磁盘
  
记忆轻推机制（Memory Nudge）

每隔 N 轮（默认 memory.nudge_interval: 10），会触发一个后台回顾线程：

_turns_since_memory >= nudge_interval
        ↓
spawn_background_review()
        ↓
fork agent (memory + skill_manage tools only)
        ↓
发送: "Review the conversation above and consider saving to memory..."
        ↓
fork agent 自主决定是否调用 memory 工具
        ↓
写入直接落到 MEMORY.md（磁盘） + 通知外部 provider

但这不影响当前会话的系统提示词（冻结快照不变），下个会话启动时才会看到新写入的记忆。

你的当前配置

yaml
memory:
memory_enabled: true       # MEMORY.md 已启用
user_profile_enabled: true # USER.md 已启用
memory_char_limit: 2200    # 记忆字符上限
user_char_limit: 1375      # 用户档案字符上限
provider: ''               # 无外部 provider（仅内置）
nudge_interval: 10         # 每10轮触发回顾
flush_min_turns: 6         # 最少6轮后才考虑写入


当前状态：
- MEMORY.md — 空（尚未学到任何持久记忆）
- USER.md — 1条记录："User communicates in Chinese (Simplified)."

总结：记忆系统层次图

┌──────────────────────────────────────────────────────┐
│                  系统提示词 (给LLM看的)                │
│   ┌──────────────────────────────────────────────┐   │
│   │  VOLATILE 层: 记忆快照注入                    │   │
│   │  "MEMORY (your personal notes) [3% — 0/2200]" │   │
│   │  "USER PROFILE [3% — 42/1375 chars]"         │   │
│   └──────────────┬───────────────────────────────┘   │
└──────────────────┼──────────────────────────────────┘
                    │ 冻结快照 (会话内不变)
┌──────────────────┼──────────────────────────────────┐
│          MemoryManager (调度中心)                     │
│   ┌──────────────┴──────────────────────────────┐   │
│   │  内置 Provider (MemoryStore)                │   │
│   │  ├── MEMORY.md  ←→  内存 state              │   │
│   │  ├── USER.md    ←→  内存 state              │   │
│   │  └── 原子写入 + 文件锁                       │   │
│   ├─────────────────────────────────────────────┤   │
│   │  外部 Provider (最多1个)                     │   │
│   │  Honcho / Mem0 / Holographic / ...          │   │
│   │  ├── prefetch()      ← 每轮前召回           │   │
│   │  ├── sync_turn()     ← 每轮后同步           │   │
│   │  └── on_memory_write() ← 桥接内置写入        │   │
│   └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
                    │
┌──────────────────┼──────────────────────────────────┐
│            工具层 (LLM 可直接调用)                     │
│   memory tool: add / replace / remove                │
│   target: "memory" (MEMORY.md) | "user" (USER.md)   │
└─────────────────────────────────────────────────────┘
                    │
┌──────────────────┼──────────────────────────────────┐
│            磁盘持久化                                  │
│   ~/.hermes/memories/MEMORY.md                       │
│   ~/.hermes/memories/USER.md                         │
└─────────────────────────────────────────────────────┘


