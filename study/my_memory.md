# 持久记忆

四块能力 re-export 出来：
prompt.py    记忆更新提示词、记忆注入格式化
queue.py     对话结束后的异步防抖更新队列
storage.py   memory.json 文件存储
updater.py   调 LLM 从对话中提炼/更新记忆

## 两种memory：
- 全局：
.deer_flow/memory.json

- custom agent，私有memory
.deer_flow/agents/<agent_name>/memory.json

## 记忆内容

它不是逐条聊天记录，而是 压缩后的长期画像
user.workContext        当前工作/项目/技术栈
user.personalContext    语言、沟通偏好、兴趣
user.topOfMind          最近关注点
history.recentMonths    最近几个月活动
history.earlierContext  更早但仍相关的模式
history.longTermBackground 长期背景
facts                   具体事实列表

## 写入时间
每次 agent 执行结束后：
1, 检查 memory.enabled
2, 拿到当前 thread_id
3, 从 state 里拿 messages
4, 过滤消息
4, 放入 memory queue
4, 30 秒 debounce 后由 MemoryUpdater 更新 memory.json


会有过滤逻辑：memory_middleware.py (line 112)：
保留：
- human 消息
- 没有 tool_calls 的最终 AI 消息

丢弃：
- ToolMessage
- 带 tool_calls 的 AI 中间步骤
- <uploaded_files> 这种临时上传路径信息

## 更新流程
核心在 updater.py (line 164)。

读取当前 memory.json
        ↓
把当前对话格式化成文本
        ↓
构造 MEMORY_UPDATE_PROMPT
        ↓
调用 LLM
        ↓
要求 LLM 返回 JSON 更新建议
        ↓
_apply_updates 合并到现有 memory
        ↓
写回 memory.json


## 防抖队列
queue.py (line 25) 做防抖更新。

如果 30 秒内同一个 thread 连续多轮对话，它会合并，只保留这个 thread 最新的一份待处理上下文：

## 容量管理
简单，
memory:
  max_facts: 100
  fact_confidence_threshold: 0.7
  max_injection_tokens: 2000

### facts 有硬上限

在updater.py (line 344)：，
```python
if len(current_memory["facts"]) > config.max_facts:
    current_memory["facts"] = sorted(
        current_memory["facts"],
        key=lambda f: f.get("confidence", 0),
        reverse=True,
    )[: config.max_facts]
```

也就是说 facts 超过 max_facts 后，会按 confidence 从高到低排序，只保留前 100 条。低置信度 facts 会被丢掉。

### summary 区域没有严格长度上限

这些字段没有字符数硬限制：
user.workContext.summary
user.personalContext.summary
user.topOfMind.summary
history.recentMonths.summary
history.earlierContext.summary
history.longTermBackground.summary

提供提示词进行限制，但每次更新是“覆盖 summary”，不是一直 append。它通常不会无限膨胀，除非 LLM 不遵守长度要求。

### 注入系统提示词时有 token 上限

在 prompt.py (line 172) 的 format_memory_for_injection(...) 会按 token budget 格式化，facts 会按置信度排序，放不下的就不注入。

所以“记忆文件变大”和“每次都塞进模型上下文”不是一回事。模型最多吃约 2000 tokens 的 memory。


## 当记忆满时，会发生什么
主要是 facts 满：

新 facts 加入
        ↓
超过 max_facts
        ↓
按 confidence 排序
        ↓
只保留前 100 条
        ↓
低置信度 facts 被删除
