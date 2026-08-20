---
title: AIAgent
created: 2026-06-22
updated: 2026-06-24
type: entity
tags: [core-class, agent-loop, init-flow]
sources: [run_agent.py, agent/agent_init.py, agent/conversation_loop.py]
---

# AIAgent

核心类，定义在 `run_agent.py`。代表一个完整的 AI agent 实例——持有 provider 连接、
tool 定义、memory 状态、guardrail 配置，并通过 `run_conversation()` 执行
tool-calling 循环。

## 文件位置

`~/.hermes/hermes-agent/run_agent.py`

## 构造函数 (~60 参数)

```python
class AIAgent:
    def __init__(self,
        base_url, api_key, provider, api_mode, model,
        max_iterations=90,
        enabled_toolsets=None, disabled_toolsets=None,
        quiet_mode=False, platform=None, session_id=None,
        skip_context_files=False, skip_memory=False,
        credential_pool=None,
        # ... 还有 ~40 个参数
    ):
```

### 为什么 60 个参数？为什么不拆？

60 个参数不是一次性设计的——是逐步堆叠的结果。每个新 feature （steer 注入、
checkpoint、grace call、prefill、service tier...）往构造函数上加一个参数。
拆分的阻力在于 **run_conversation() 里有 50+ 个 `agent.xxx` 点引用**。如果拆成
`ModelConfig` / `SessionIdentity` / `AgentHooks` 三个子对象，所有引用都要改。

业内对比：
- **LangChain 等**：Builder 模式，参数分组到 `.with_model()` / `.with_tools()` 等
- **Google Cloud / K8s client-go**：Context 对象，一个 struct 包所有
- **Hermes**：大参数列表，但 `__init__` 只是薄壳转发

Hermes 选这派的理由：IDE 精确补全、Python kwargs 自然兼容（gateway 用
`**_agent_cbs(sid)` 一次注入 10 个 callback）、不需要跳转另一个类看定义。

代价：新贡献者第一眼看到 60 个参数会晕。团队自己也承认——AGENTS.md 写的是
"Read `run_agent.py` for the full list"。

## 初始化的真正执行者：init_agent

`AIAgent.__init__` 是一个 **thin forwarder**：

```python
class AIAgent:
    def __init__(self, ...60 params...):
        from agent.agent_init import init_agent
        init_agent(self, ...)  # 把自己传给外部函数
```

`init_agent(agent, ...)` 位于 `agent/agent_init.py`，约 1500 行。核心逻辑就是：

```python
def init_agent(agent, ...):
    agent.model = model           # 等价于 self.model = model
    agent.max_iterations = ...
    agent.platform = platform
    agent._interrupt_requested = False
    agent._tool_guardrails = ToolCallGuardrailController()
    agent.tools = get_tool_definitions()
    agent.client = _create_openai_client()
    # ... ~100 个属性赋值
```

这里的 `agent` 就是外面的 `self`。Python 里没有 `__slots__` 的类可以在运行时
动态添加任意属性——`init_agent` 利用了这一点，往 `agent.__dict__` 上粘了 100+
个键值对。

**为什么不直接在 `AIAgent.__init__` 里写 `self.xxx = yyy`？**

因为原来的 `run_agent.py` 是 12000 行。把 1500 行初始化逻辑搬到独立文件
`agent/agent_init.py` 是为了物理拆分——IDE 不卡、git blame 有意义、code review
不淹没。拆成外部函数而不是类方法，是为了**不改 AIAgent 的签名**且让
`agent_init` 可以独立 import 和测试。

初始化流程分六个阶段：
1. Provider 解析 + OpenAI 客户端创建（L520-790）
2. 工具加载（toolset → check_fn 过滤 → ~45 个 schema）（L818）
3. Guardrail / budget / compressor 创建（L344-432）
4. Memory 加载（built-in + 外部 provider）
5. Callback 注入（L327-338）
6. System prompt 延迟构建（首次 `run_conversation()` 时触发）

## 关键属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `model` | str | 模型名（如 `deepseek-v4-pro`） |
| `provider` | str | provider 标识 |
| `tools` | list[dict] | OpenAI-format tool schemas |
| `valid_tool_names` | set[str] | 可用 tool 的名称集合 |
| `max_iterations` | int | 单次 conversation 最大 API 调用次数 |
| `_memory_store` | MemoryStore | built-in memory（frozen snapshots） |
| `_memory_manager` | MemoryManager | 外部 memory provider 编排器 |
| `_tool_guardrails` | ToolCallGuardrailController | tool 调用 guardrail |
| `_cached_system_prompt` | str | 延迟构建的 system prompt 缓存 |
| `_interrupt_requested` | bool | 用户请求中断标志 |
| `save_trajectories` | bool | 是否保存 trajectory 到 JSONL |

## 关键方法

| 方法 | 说明 |
|------|------|
| `chat(message)` | 简单接口，返回 `final_response` 字符串 |
| `run_conversation(user_message, ...)` → dict | 完整接口，返回 `{final_response, messages}` |

`run_conversation()` 是一个 thin forwarder，实际逻辑在 `agent/conversation_loop.py`。

## 关系

- [[conversation-loop]] — 真正的 while tool-calling 循环，约 4000 行
- [[memory-system]] — `_memory_store` 和 `_memory_manager`
- [[tool-system]] — `tools` / `valid_tool_names`
- [[guardrail-system]] — `_tool_guardrails`
- [[iteration-budget]] — `iteration_budget`
- [[context-compression]] — `context_compressor`

## 设计要点

- AIAgent 本身不包含循环逻辑——构造函数只做初始化，循环在 `conversation_loop.py`
- System prompt 是延迟构建的，不是 `__init__` 时构建，因为需要首轮对话上下文
- `quiet_mode` 关闭所有 stdout 输出（用于子 agent、gateway、cron）
- `skip_memory=True` 可以让 agent 完全绕过记忆系统
- **60 个参数是演进结果，不是设计决策。** 物理文件拆分（`agent_init.py` 独立）是第一步，
  逻辑拆分（Context/Bundle 对象）尚未进行
