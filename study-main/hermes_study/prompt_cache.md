# LLM 提供商层面的机制

你每次调用 API 时，发送的是这样的请求体：
```json
POST /v1/chat/completions
{
    "model": "deepseek-v4-pro",
    "messages": [
    {"role": "system", "content": "你是 Hermes...（约 15KB）"},
    {"role": "user", "content": "帮我写代码"}
    ],
    "tools": [...]
}
```

Transformer 模型处理每个 token 时，需要计算 Key-Value 缓存（KV Cache）——这是注意力机制产生的中间计算结果。计算 KV cache 很昂贵（占推理时间的 50-70%）。

Prompt Cache 的核心思想：如果你连续两次请求的前缀（prefix）完全相同，提供商可以复用第一次算好的 KV cache，跳过重复计算。

请求 1:  [system: 你是Hermes... | user: 帮我写代码]     ← 完整计算，35000 tokens
请求 2:  [system: 你是Hermes... | user: 再优化一下]     ← 前缀命中！只需计算 "再优化一下" 5 tokens
                                ↑
                    前 34995 tokens 的 KV cache 被复用

这带来的实际收益：

| | 无缓存 | 有缓存 |
|---|---|---|
| 首 token 延迟 | 3-5 秒 | 0.3-0.5 秒 |
| 输入 token 费用 | 100% | 10-25%（缓存命中部分打折） |
| 总处理时间 | 全量计算 | 增量计算 |

不同提供商的实现：

- Anthropic：显式的 cache_control: {"type": "ephemeral"} 标记，5 分钟 TTL，缓存命中部分按 10% 计费
- DeepSeek：自动前缀匹配（无需显式标记），缓存命中部分按 50% 计费
- OpenAI：自动前缀匹配，缓存命中部分按 50% 计费
- OpenRouter：透传下游提供商的缓存机制

核心约束：缓存只认字节完全相同的连续前缀。哪怕你改了系统提示词里的一个空格，整个 KV cache 全部作废。


# Hermes 如何保证 Prompt 稳定

Hermes 的系统提示词非常长（技能列表 + 工具指导 + 记忆 + 时间戳等），大约 15000-35000 tokens。如果每次轮次之间系统提示词发生任何变化，这些 token 的 KV cache 都会作废。

所以 Hermes 的整个系统提示词构建流程被设计成三层架构，只有一个目标：让系统提示词在整个 session 中完全不变。

## 机制一：三层分拆 + 一次性构建

┌────────────────────────────────────────────────────────────┐
│ 系统提示词 = stable + context + volatile                    │
│                                                            │
│ stable 层（会话内永远不变）：                                │
│   SOUL.md / DEFAULT_AGENT_IDENTITY                          │
│   工具使用指导（MEMORY_GUIDANCE、SKILLS_GUIDANCE 等）       │
│   全部 Skills 索引                                          │
│   WSL 环境提示 / 平台提示（CLI）                             │
│                                                            │
│ context 层（会话内不变）：                                   │
│   AGENTS.md / .cursorrules 项目上下文                       │
│   system_message                                           │
│                                                            │
│ volatile 层（会话内不变！！！）：                             │
│   记忆快照 ← 使用冻结快照，不是实时记忆                       │
│   时间戳 ← 只有日期，没有分钟（Friday, May 29, 2026）      │
│   Model + Provider 字符串                                   │
└────────────────────────────────────────────────────────────┘

关键在 agent/system_prompt.py 第 287-303 行：

```python
def build_system_prompt(agent, system_message=None):
    """
    Called once per session (cached on agent._cached_system_prompt) and
    only rebuilt after context compression events. This ensures the system
    prompt is stable across all turns in a session, maximizing prefix cache hits.
    """
    parts = build_system_prompt_parts(agent, system_message=system_message)
    return "\n\n".join(parts["stable"], parts["context"], parts["volatile"])    
```

然后在 conversation_loop.py 中：
```python
if agent._cached_system_prompt is None:
    _restore_or_build_system_prompt(agent, system_message, conversation_history)
active_system_prompt = agent._cached_system_prompt
```
→ 整个 session 的每一轮都用同一个 active_system_prompt

## 机制二：冻结快照 —— 记忆更新不影响提示词

```python
工具 "memory" add → 更新内存 state + 写入 MEMORY.md 磁盘
但是！_system_prompt_snapshot 不变！
format_for_system_prompt() 永远返回 load_from_disk() 时的快照
```
时间戳也只精确到日期：

```python
system_prompt.py 第 271 行
timestamp_line = f"Conversation started: {now.strftime('%A, %B %d, %Y')}"
不是 "Friday, May 29, 2026 02:25:31 PM"
而是 "Friday, May 29, 2026"
→ 同一天的每一秒都一样，byte-stable
```

## 机制三：SQLite 持久化 —— 跨进程恢复

CLI 模式下每次 hermes 启动是新进程，那怎么复用上次的 prompt？

```python
def _restore_or_build_system_prompt(agent, system_message, conversation_history):
    # 1. 先从 SQLite 尝试恢复
    if conversation_history and agent._session_db:
        stored_prompt = agent._session_db.get_session(session_id)["system_prompt"]
        if stored_prompt:
            agent._cached_system_prompt = stored_prompt  # ← 原样复用！
            return

    # 2. 首次构建
    agent._cached_system_prompt = agent._build_system_prompt(system_message)

    # 3. 立即写入 SQLite，供下次恢复
    agent._session_db.update_system_prompt(session_id, agent._cached_system_prompt)
```
这样 gateway（Telegram/Discord）模式下，同一个 chat 的每次新消息都能复用上一次完全相同的系统提示词。CLI 模式如果你用 hermes --continue 或 /resume，也能复用。
    
这个行为在源码注释中写得很直白（第 135-137 行）：
    
```python
Continuing session — reuse the exact system prompt from the previous turn so the Anthropic cache prefix matches. agent._cached_system_prompt = stored_prompt
```

## 机制四：只在压缩时重建

什么时候系统提示词会被重建？答案是 只有在上下文压缩时：
    
```python
system_prompt.py 第 306-314 行
def invalidate_system_prompt(agent):
    """Called after context compression events."""
    agent._cached_system_prompt = None
    if agent._memory_store:
        agent._memory_store.load_from_disk()  # 重新加载记忆到快照
``` 
    
压缩是"迫不得已"的——旧的对话消息太多，必须删掉一些。此时重建系统提示词是必要之恶，新的提示词会在下一轮缓存。

## 机制五：Anthropic 显式 cache_control 标记

对于 Anthropic 原生 API，Hermes 会在消息中插入 cache_control 断点。agent/prompt_caching.py：
    
```python
def apply_anthropic_cache_control(api_messages, cache_ttl="5m"):
    """
    system_and_3 策略：
    - system prompt 处放 1 个断点
    - 最后 3 条非 system 消息各放 1 个断点
    - 共最多 4 个断点，TTL 5 分钟
    """
    marker = {"type": "ephemeral"}  # 5m 默认

    # 第一条如果是 system → 标记它
    if messages[0]["role"] == "system":
        _apply_cache_marker(messages[0], marker)

    # 最后 3 条非 system 消息也标记
    non_sys = [i for i in range(len(messages)) if messages[i]["role"] != "system"]
    for idx in non_sys[-3:]:
        _apply_cache_marker(messages[idx], marker) 
``` 
这告诉 Anthropic：把这几个位置作为缓存边界。多轮对话时，system prompt + 历史消息的 KV cache 全被复用。

## 完整流程
```
完整流程图

Session 开始
│
├─ 从 SQLite 恢复 _cached_system_prompt ?
│   ├─ 有 → 原样复用 (prefix cache hit!)
│   └─ 没有 → 首次构建:
│       ├─ stable 层: SOUL.md + 技能索引 + 工具指导 + 环境提示
│       ├─ context 层: AGENTS.md + system_message
│       ├─ volatile 层: 记忆冻结快照 + 日期(无分钟) + model/provider
│       └─ 写入 SQLite
│
▼
Turn 1: [cached_system_prompt | user: "写个脚本"]
    → LLM 完整计算所有 token
    → 提供商缓存 system prompt 的 KV cache

Turn 2: [cached_system_prompt | user: "写个脚本" | assistant | user: "加个功能"]
                                    ↑ 完全相同！              ↑ 拼接在后面
    → LLM 跳过 system prompt，从上次的 user message 之后开始算
    → 输入 token 费用减少 ~75%

Turn 3: [cached_system_prompt | ...历史... | user: "再改一下"]
    → 同上，前缀缓存持续命中

Turn N (需要压缩):
    → invalidate_system_prompt() → _cached_system_prompt = None
    → 压缩历史消息
    → 重建系统提示词 (包含更新的记忆)
    → Turn N+1 重新开始缓存
```


总结
    
| 层面 | 做了什么 | 效果 |
|------|---------|------|
| LLM 提供商 | 提供 KV cache 复用（Anthropic 显式标记 / DeepSeek 自动检测） | 跳过已缓存前缀的计算 |
| Hermes 系统提示词 | 三层分拆 + _cached_system_prompt 一次构建全 session 复用 | 确保前缀 byte-stable |
| 记忆系统 | 冻结快照模式 | 写入新记忆不改变系统提示词 |
| 时间戳 | 精度只到日期 | 同一天内的所有轮次前缀相同 |
| SQLite 持久化 | 存储 _cached_system_prompt 到 session DB | 跨进程/跨重启恢复，gateway 续接命中 |
| 压缩 | 只在必须压缩时 invalidate_system_prompt() | 只在万不得已时牺牲缓存 |
