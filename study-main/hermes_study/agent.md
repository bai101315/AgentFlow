# 创建方式
就是 AIAgent() ，一个普通 Python 类实例

```python
run_agent.py 第 326 行
class AIAgent:
    def init(self, base_url, api_key, provider, model, ...):
        from agent.agent_init import init_agent
        init_agent(self, ...)   # 注入 100+ 个属性到 self 上
```

没有 Builder 模式，没有工厂函数，没有 create_xxx_agent()。就是一个 Python 类，init 接收约 60 个参数（model、provider、callback、credentials...），然后委托给 init_agent() 函数把属性直接打到 self 上：

```python
self.model = "deepseek-v4-pro"
self.provider = "deepseek"
self.max_iterations = 90
self.tools = [...]       # 过滤好的 tool schemas
self._memory_store = ... # 持久记忆
self.valid_tool_names = set(...)  # 可用工具名集合
self.base_url = "https://api.deepseek.com"
... 约 100+ 个属性
```

调用方式极其简单：```agent = AIAgent()```

## 为什么要有60个参数？为什么直接传参

AIAgent 覆盖的业务面太广了，简单说就是 一个类，多套前端。看调用方：

调用方: CLI one-shot
文件: hermes_cli/oneshot.py:313
传的参数: 15个左右
────────────────────────────────────────
调用方: TUI gateway
文件: tui_gateway/server.py:1906
传的参数: ~20个
────────────────────────────────────────
调用方: 还有 Telegram gateway、Discord gateway、cron scheduler、batch runner、delegate_task...

60个参数其实分为四组：
```
1. 模型/API 配置 (~12 个)：base_url, api_key, provider, api_mode, model, max_tokens, reasoning_config, service_tier, request_overrides, fallback_model, max_iterations, tool_delay

2. 会话/平台身份 (~10 个)：session_id, platform, user_id, user_name, chat_id, chat_name, chat_type, thread_id, gateway_session_key, parent_session_id

3. 回调函数 (~10 个)：tool_progress_callback, tool_start_callback, tool_complete_callback, thinking_callback, reasoning_callback, clarify_callback, step_callback, stream_delta_callback, interim_assistant_callback, tool_gen_callback, status_callback

4. 开关/feature flag (~15 个)：quiet_mode, verbose_logging, save_trajectories, skip_context_files, skip_memory, load_soul_identity, checkpoints_enabled, checkpoint_max_snapshots, pass_session_id...
```

## 业内派系

### 瘦构造函数 + Builder/Config（主流）
LangChain、大多数 Java/.NET 框架走这条路：
```python
agent = AgentBuilder()
    .with_model("gpt-4")
    .with_tools([...])
    .with_callbacks([...])
    .build()
```
优点：渐进式构造，文档自解释，IDE 补全友好。每个 builder 方法接收的是"相关参数组"，不会一把 60 个参数糊脸上。
缺点：多了一层抽象，builder 本身也要维护。而且 builder 暴露给用户的 API 往往比构造函数还大——只是组织方式不同。

### Context 对象
```python
ctx = AgentContext(
    model_config=...,
    session_identity=...,
    platform_hooks=...,
)
agent = Agent(ctx)
```
优点：参数数骤减到 1-2 个，传配置就像传一个背包；后续加字段不破坏签名。
缺点：类型变得模糊，IDE 不知道 ctx 里到底有什么，需要运行时探索。

### hermes-agent 现在的方式

优点（确实有）：
- 自文档——看签名就知道 agent 吃哪些东西，不需跳转另一个类
- IDE 静态分析友好——pylance 精确提示每个参数名和类型
- 没有中间对象——调用方直接传值，少一次堆分配
- Python kwargs 天然兼容——TUI 那边用 **_agent_cbs(sid) 解包一把注入，零摩擦

缺点（很痛）：
- 60 个参数，新贡献者打开 AGENTS.md 就晕
- 加一个新 feature flag（如 checkpoint_max_file_size_mb）就要在 AIAgent.init → init_agent → 内部的尾部链路都改一遍，容易漏
- 很多参数在大多数调用方都是默认值（oneshot 模式不需要 clarify_callback，但它依然在签名里占位置）
- 测试 Mock 痛苦——构造一个能用的 AIAgent 需要知道其全部 60 个参数的默认语义

目前的问题还没有大到非改不可——因为所有调用方（CLI、TUI、gateway、batch）都有自己的一层函数封装（_build_agent() 之流），所以对业务代码来说，60 个参数的痛苦已经被隔离了，暴露的只有核心开发者。


# init_agent
```python
class AIAgent:
    def init(self, ...):
        init_agent(self, ...)     # self 就是那个 Agent 实例
                                    # 传进去之后在 init_agent 里叫 agent
def init_agent(agent, ...):
    agent.model = model           # 这里的 agent 就是外面的 self
    agent.platform = platform     # 完全等价于 self.model = model
    agent._interrupt_requested = False
```
这里面的agent就是self，这么做的理由：
```
1. 不想碰 init 的签名。AIAgent 有 60 个参数，如果把初始化逻辑抽成方法，方法的参数也会是 60 个——区别只是 def init(...) 变成 def _init_internals(self, ...)，视觉上没区别，拆不拆都难看。

2. 物理文件拆分。把 1500 行初始化代码从 run_agent.py 搬到 agent/agent_init.py，run_agent.py 从 12000 行降到 ~4000 行，agent_init.py 独立成为一个 1504 行的文件。IDE 打开不卡了，git blame 有意义了，code review 不会被 diff 淹没。

3. agent_init.py 可以被单独 import、单独测试。如果逻辑写在 AIAgent 内部作为方法，测试时你得构造一个完整的 AIAgent 实例或者 mock 它。现在 init_agent 是一个普通函数，测试可以直接传一个 mock 对象进去验属性。
```


# 核心循环

只有两个入口```run_conversation```和```chat```, 
```python
def run_conversation(
    self,
    user_message: str,
    system_message: str = None,
    conversation_history: List[Dict[str, Any]] = None,
    task_id: str = None,
    stream_callback: Optional[callable] = None,
    persist_user_message: Optional[str] = None,
) -> Dict[str, Any]:
    """Forwarder — see ``agent.conversation_loop.run_conversation``."""
    from agent.conversation_loop import run_conversation
    return run_conversation(self, user_message, system_message, conversation_history, task_id, stream_callback, persist_user_message)

def chat(self, message: str, stream_callback: Optional[callable] = None) -> str:
    """
    Simple chat interface that returns just the final response.

    Args:
        message (str): User message
        stream_callback: Optional callback invoked with each text delta during streaming.

    Returns:
        str: Final assistant response
    """
    result = self.run_conversation(message, stream_callback=stream_callback)
    return result["final_response"]
```

Hermes 使用的确实是 ReAct（Reasoning + Acting） 模式，但不是调任何库。核心循环在 conversation_loop.py，自己用 while 写的：

    
    run_conversation(user_message) 
    │
    ├─ L215-280  前置准备（stdio guard, session tag, skill origin, fallback 恢复）
    ├─ L280-400  状态重置 & 内存水合（retry counter, budget, todo hydration, nudge 计数）
    ├─ L400-525  构建 message list + preflight 上下文压缩
    ├─ L526      主循环入口 ──────────────────────────────────────────────┐
    │                                                                      │
    │  ┌──── while (iteration < max AND budget > 0) or grace_call: ───┐   │
    │  │                                                                │   │
    │  │  L600-660   中断检查 / steer 注入 / step callback              │   │
    │  │  L660-880   构建 API messages（system prompt + prefill + tools）│   │
    │  │  L880-1050  KawaiiSpinner 启动                                 │   │
    │  │  L936-1270  内层 retry 循环 ─── 实际 API 调用                  │   │
    │  │             │  ├─ streaming / non-streaming 分支               │   │
    │  │             │  ├─ 网络错误 → backoff + retry                   │   │
    │  │             │  ├─ context 超长 → 压缩 → 清空 history → retry   │   │
    │  │             │  ├─ rate limit → 等冷却 → retry                  │   │
    │  │             │  └─ 成功 → 拿到 response                         │   │
    │  │  L1270+    各种错误分支的 return（含 early exit）               │   │
    │  │  L3105     工具调用处理                                         │   │
    │  │             │  ├─ 校验 tool name（防幻觉）                      │   │
    │  │             │  ├─ 校验 JSON 参数                               │   │
    │  │             │  ├─ 执行 handle_function_call()                  │   │
    │  │             │  └─ 结果塞回 messages                            │   │
    │  │  L3360     上下文压缩检查（should_compress）                    │   │
    │  │  L3412     增量保存 session log                                 │   │
    │  │  L3520     跑回 while 顶部，下一轮迭代                          │   │
    │  └────────────────────────────────────────────────────────────────┘   │
    │                                                                      │
    ├─ L3858  保存 trajectory（如启用）                                     │
    ├─ L3940  Plugin hook: transform_llm_output                             │
    ├─ L3960  Plugin hook: post_llm_call                                    │
    ├─ L3990  提取 final_response                                           │
    ├─ L4060  后台 review nudge（memory/skill 自动保存提示）                 │
    ├─ L4079  Plugin hook: on_session_end                                   │
    └─ L4095  return result

理解需求：
```
1. 想理解正常流程（一个 tool call 怎么走完）→ 从 L598 的 while 开始，跳到 L3105 的 tool call 处理，然后跟回循环顶部
2. 想理解错误处理（context 超长怎么办）→ 从 L2300 的 compression_attempts 开始往下看
3. 想加新 feature → 搞清楚你的 feature 插在流水线的哪个阶段（前置准备？主循环内？post-turn？），只看那个区块
4. 想看 API 调用细节 → L936-L1270 的 retry 循环
```

说实话，4000 行函数在 Python 圈是异类，但在"一个主循环驱动所有事情"的游戏引擎里（Unity 的 Update(), Unreal 的 Tick()）并不罕见。hermes-agent 的 run_conversation 本质就是一个单帧 tick——只是这个帧可能要跑 90 次迭代。



## 和create_agent()区别

和 LangChain create_agent 的本质区别

| | Hermes AIAgent | LangChain create_agent |
|---|---|---|
| 本质 | 一个普通 Python 对象 + while 循环 | 框架内的 Runnable 图节点 |
| 创建 | agent = AIAgent(...) 直接 new | create_agent(llm, tools) 返回 CompiledGraph |
| 循环 | 裸 while 循环，自实现 | LangGraph 状态机 add_node/add_edge |
| 状态 | 实例属性 self.xxx | TypedDict 状态对象，通过节点传递 |
| 工具格式 | 原生 OpenAI function-calling dict | @tool 装饰器 + BaseTool 抽象 |
| 提示词 | 自己拼字符串 | ChatPromptTemplate + MessagesPlaceholder |
| 中间件 | 无（函数直调） | RunnableLambda / RunnablePassthrough |
| 依赖 | 只依赖 openai SDK | langchain-core + langgraph + langchain |
| 可观测性 | 自己打 log | LangSmith / callbacks |
| 灵活性 | 直接改源码的 while 循环 | 插拔式中间件，但受框架约束 |


**langchain**底层是一个编译好的状态图:
```agent (LLM) → tools (conditional) → agent → END```
hermes:
```while 循环 { call_llm() → if tool_calls: execute() → append → continue else: return }```

## Hermes 拒绝框架的深层原因

1. 直接操作 messages 数组
    Hermes 的对话状态就是纯 Python List[Dict]，每条消息是 {"role": "user/assistant/tool", "content": ...}。没有任何封装。LangChain 需要 BaseMessage / HumanMessage / AIMessage 等类型的包装。

2. prompt cache 是首要设计约束
   系统提示词在会话内必须保持 byte-stable，否则 Anthropic/DeepSeek 的 prefix cache 失效。框架的中间件和模板渲染会让这一点变得不可控。

3. Provider 差异在 transport 层解决，不变更核心循环
   DeepSeek 和 Anthropic 和 Gemini 的 API 差异只在 agent/transports/ 下的 build_kwargs() 里处理。核心 while 循环对所有 provider 完全一样。

4. 工具就是 dict，不是类
    不需要 @tool 装饰器，不需要 BaseTool 继承。就用 OpenAI 原生的 JSON Schema dict + 一个 handler 函数。

这不是 LangChain 那种"组装 DAG 图然后编译执行"的范式，而是最直接的"接收输入 → while 调用 LLM → 执行工具 → 返回结果"的命令式代码。
    
为什么不用 LangChain？ 因为当你需要精细控制 prompt cache、跨 20 个 provider、子代理 fork、上下文压缩、记忆 nudge、后台回顾线程、文件系统检查点这些功能时，框架的抽象层会成为障碍，直接写 while 循环反而最简单。


## agent 初始化

## 阶段 0：CLI 启动，读取配置
    

    │
    ├─ cli.py: HermesCLI.init()
    │   ├─ 读取 ~/.hermes/config.yaml
    │   ├─ 读取 ~/.hermes/.env → 加载 API keys
    │   │
    │   ├─ self.model = "deepseek-v4-pro"     ← config.yaml → model.default
    │   ├─ self.provider = "deepseek"          ← config.yaml → model.provider
    │   ├─ self.base_url = "https://api.deepseek.com"
    │   │
    │   ├─ self.enabled_toolsets = _get_platform_tools(config, "cli")
    │   │   └─ 读取 config.yaml → platform_toolsets.cli = ["hermes-cli"]
    │   │
    │   └─ self.max_turns = agents.max_turns = 90

## 阶段 1：创建 AIAgent 实例

cli.py 第 4516 行:
      │
self.agent = AIAgent(
    model="deepseek-v4-pro",
    provider="deepseek",
    base_url="https://api.deepseek.com",
    api_key="sk-5157...",
    enabled_toolsets=["hermes-cli"],    ← 从这里来
    max_iterations=90,
    platform="cli",
    session_db=...,
    ...约 40 个参数
)
│
└─→ run_agent.py: AIAgent.init()
    └─→ agent/agent_init.py: init_agent(self, ...)

在 agent_init中会解析很多东西，然后绑定给agent

## 阶段 2：解析 Provider + Credentials

agent_init.py:
    │
    ├─ 解析 provider → "deepseek"
    ├─ 解析 api_mode → "chat_completions"（OpenAI 兼容协议）
    ├─ 建立 OpenAI client (api.deepseek.com)
    ├─ 获取 model metadata（context length 等）
    │
    └─ agent.reasoning_config = {...}
        agent.max_tokens = None
        agent.prefill_messages = []

## 阶段 3：加载工具 — Toolset 过滤 + Schema 生成

agent_init.py 第 818 行:
    │
    agent.tools = get_tool_definitions(
        enabled_toolsets=["hermes-cli"],
        disabled_toolsets=[],
    )
    │
    ├─ "hermes-cli" 是复合 toolset
    │   └─ 展开 includes: ["web","browser","terminal","file",...]
    │       └─ 进一步展开每个子 toolset
    │           └─ 得到 ~70 个工具名
    │
    ├─ 对每个工具名:
    │   ├─ registry.get_entry(name)
    │   ├─ check_fn()? → 通过才加入  (如 browser 工具检查 API key)
    │   ├─ dynamic_schema_overrides? → 更新描述（如 delegate_task 的并发数）
    │   └─ 组装为 {"type":"function","function":{name,description,parameters}}
    │
    ├─ 过滤完后约 45 个工具 schema
    │
    └─ agent.tools = [45 个 OpenAI function-calling 格式 dict]
        agent.valid_tool_names = {"read_file","write_file","terminal",...}
    
## 阶段 4：加载记忆 — MemoryStore 初始化


## 阶段 7：首次构建系统提示词（延迟执行）

系统提示词不在 init 时构建，而是延迟到 run_conversation() 首次调用时：
conversation_loop.py 第 417 行:
    │
    if agent._cached_system_prompt is None:
        _restore_or_build_system_prompt()
        │
        ├─ 检查 SQLite 是否有存储的 prompt?
        │   └─ 新会话 → 没有 → 首次构建
        │
        └─ agent._build_system_prompt(system_message)
            │
            └─→ system_prompt.py: build_system_prompt()
                    │
                    ├─ STABLE 层:
                    │   ├─ load_soul_md()  → /home/bai/.hermes/SOUL.md
                    │   ├─ DEFAULT_AGENT_IDENTITY（如果没有 SOUL.md）
                    │   ├─ MEMORY_GUIDANCE     ← 因为有 memory 工具
                    │   ├─ SESSION_SEARCH_GUIDANCE
                    │   ├─ SKILLS_GUIDANCE      ← 因为有 skill_manage 工具
                    │   ├─ build_skills_system_prompt()
                    │   │   └─ 扫描 ~/.hermes/skills/ → 生成技能索引
                    │   ├─ build_environment_hints() → "WSL" 提示
                    │   └─ PLATFORM_HINTS["cli"] → CLI 平台提示
                    │
                    ├─ CONTEXT 层:
                    │   ├─ system_message（如果有）
                    │   └─ build_context_files_prompt() → AGENTS.md
                    │
                    ├─ VOLATILE 层:
                    │   ├─ MEMORY.md 冻结快照  [0/2200 chars]
                    │   ├─ USER.md 冻结快照    [3%/1375 chars]
                    │   ├─ 外部 provider block  （无）
                    │   └─ "Conversation started: Monday, June 2, 2026"
                    │       "Model: deepseek-v4-pro"
                    │       "Provider: deepseek"
                    │
                    └─ 拼成大字符串 → agent._cached_system_prompt
                        └─ 存入 SQLite 供后续恢复

