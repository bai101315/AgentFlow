# AgentFlow 项目面试复盘与完整答案

这份文档用于复习 AgentFlow 项目面试。每个问题都包含：面试问题、你当前暴露的问题、完整回答范例、重点代码。  
建议你不要死背，而是先理解代码，再把“完整回答范例”改写成自己的口吻。

## 复习优先级

优先关注这 5 个方向：

1. `tool_search` 延迟工具发现
2. Agent 生命周期与上下文隔离
3. Checkpointer 与长期记忆的职责边界
4. 本地安全沙箱与路径校验
5. Middleware 架构与执行治理

这些都是你简历里写得比较重、面试官很容易追问的点。

## 问题 1：延迟工具发现 tool_search

**面试问题：**

当 `tool_search.enabled = true` 时，一个 MCP 工具从“注册进 Agent”到“被模型真正调用成功”，中间经历了哪些步骤？

**你当前的问题：**

- 你说到了名字、描述、注册表和搜索，但没有讲清楚 MCP 工具仍然在 Agent 的执行层 tools 里。
- 漏了 `DeferredToolFilterMiddleware.wrap_model_call()` 和 `wrap_tool_call()` 的职责。
- 没有明确说出 `promote()` 之后，下一轮模型调用时 schema 才会暴露。

**完整回答范例：**

开启 `tool_search.enabled` 之后，我并不是把 MCP 工具完全从 Agent 里移除，而是把它们做成“执行层可用、模型层延迟暴露”的状态。

具体流程是：系统启动或懒加载时，会从 MCP server 拉取工具，并把这些工具统一成 LangChain 的 `BaseTool`。在 `get_available_tools()` 里，如果开启了 `tool_search`，这些 MCP 工具会被注册到 `DeferredToolRegistry`。注册表里保存的是工具的轻量元信息和完整工具对象，包括 `name`、`description` 和 `tool` 本体。

然后这些 MCP 工具仍然会进入最终的 tools 列表，交给 LangGraph 的工具执行层。但是在模型调用前，`DeferredToolFilterMiddleware.wrap_model_call()` 会拦截 `request.tools`，把仍然处于 deferred 状态的工具 schema 过滤掉。这样模型一开始看不到完整参数 schema，只能在 prompt 的 `<available-deferred-tools>` 里看到工具名字，知道有这些能力存在。

如果模型需要某个工具，就先调用 `tool_search`，比如 `tool_search("select:xxx")`。`tool_search` 会从 `DeferredToolRegistry` 里搜索对应工具，用 LangChain 的 `convert_to_openai_function()` 转成标准 function schema 返回给模型。同时它会调用 `registry.promote()`，把这些工具从 deferred registry 里移除。

下一轮模型调用时，`DeferredToolFilterMiddleware` 再过滤工具列表时，已经 promoted 的工具不再属于 deferred names，所以它的完整 schema 会进入 `bind_tools`，模型就可以正常生成这个工具的调用。

还有一个安全兜底：如果模型没有先调用 `tool_search`，而是直接调用某个 deferred 工具，`wrap_tool_call()` 会拦截并返回错误，提示模型先通过 `tool_search` 暴露 schema 再重试。

所以这个机制的核心是：MCP 工具仍然在执行层可用，但 schema 不一开始全部塞进 prompt。模型按需搜索、按需 promote，从而降低 prompt token 开销，同时保持工具可用性。

**重点代码：**

- `backend/tools/builtins/tool_search.py`
- `backend/agents/middlewares/deferred_tool_filter_middleware.py`
- `backend/tools/tools.py`
- `backend/agents/lead_agent/prompt.py`

## 问题 2：为什么用 ContextVar

**面试问题：**

为什么 `DeferredToolRegistry` 用 `ContextVar`，而不是普通模块级全局变量？

**你当前的问题：**

- 你说成了“每个进程”，不够准确。这里重点是同一 Python 进程内的多请求、多协程、多 Agent 并发。

**完整回答范例：**

我这里用 `ContextVar` 是为了保证延迟工具注册表具备请求级隔离。因为 AgentFlow 可能同时运行多个用户会话或多个 Agent，每次运行时可用的 MCP 工具组、已搜索并 promoted 的工具状态都可能不同。

如果用普通模块级全局变量，所有并发请求会共享同一个 `DeferredToolRegistry`。这样 A 会话调用 `tool_search` 后把某个工具 promote 了，B 会话可能也会突然看到这个工具 schema；或者 B 会话重新初始化 registry，把 A 会话正在使用的 registry 覆盖掉，导致工具搜索结果丢失、权限边界混乱、上下文 token 控制失效。

`ContextVar` 可以让每个异步任务上下文持有自己的 registry。这样同一个进程内即使有多个 Agent 并发执行，也不会互相覆盖 deferred tool 状态，保证工具发现、promote 和调用链路只作用于当前请求。

**重点代码：**

- `backend/tools/builtins/tool_search.py`

## 问题 3：自定义 Agent 生命周期与隔离

**面试问题：**

一个自定义 Agent 是如何被创建、保存、加载并参与运行的？你是怎么保证不同 Agent 之间的人设、记忆和工作目录隔离的？

**你当前的问题：**

- 你把 `create_agent()` 说成了自定义 Agent 的持久化创建入口，这不准确。
- 工作区不是直接按 `agent_name` 建，而是 `agent_name -> thread_id -> workspace`。
- 记忆隔离没有讲完整。

**完整回答范例：**

我把自定义 Agent 抽象成一组可持久化配置，而不是只存在内存里的对象。创建 Agent 时，用户输入 Agent 名称和功能描述，系统会生成规范化的 `SOUL.md`，用于定义这个 Agent 的角色、人设、沟通方式、边界和失败处理策略。同时会在 `.deer-flow/agents/{agent_name}/` 下保存 `config.yaml` 和 `SOUL.md`。

`SOUL.md`是如何生成的？
1，拿到name和description，会自己创建一个agent,具有自己的提示词，然后会返回文本，写入文件中；2，会有兜底逻辑，使用完整的SOUL.md格式，只传入name和description


运行时，`make_lead_agent()` 会从 `config.configurable.agent_name` 读取当前 Agent 名称，然后通过 `load_agent_config(agent_name)` 加载模型配置、工具组、技能配置等，通过 `load_agent_soul(agent_name)` 把对应的 `SOUL.md` 注入 system prompt。这样不同 Agent 的角色设定不会混在一起。

记忆方面，`MemoryMiddleware` 初始化时传入 `agent_name`，对话结束后会把过滤后的用户输入和最终回答写入该 Agent 对应的 `memory.json`。下一次构建 prompt 时，系统也会按 `agent_name` 加载对应记忆，所以长期记忆是按 Agent 隔离的。

工作区方面，我不是直接用 Agent 名作为 workspace 目录，而是维护了 `agent_name -> thread_id` 的映射。每个 Agent 有稳定的 thread_id，`ThreadDataMiddleware` 根据 thread_id 生成独立的 workspace、uploads、outputs 路径，比如 `.deer-flow/threads/{thread_id}/user-data/workspace`。这样可以避免不同 Agent 的文件产物互相覆盖。

最后，加载 Agent 本质上就是切换当前 `agent_name`，重新构建 Agent 运行实例，并加载对应的 config、SOUL、memory 和 thread workspace。

**重点代码：**

- `main.py`
- `backend/tools/builtins/setup_agent_tool.py`
- `backend/config/agents_config.py`
- `backend/config/paths.py`
- `backend/agents/lead_agent/agent.py`
- `backend/agents/lead_agent/prompt.py`
- `backend/agents/middlewares/memory_middleware.py`

## 问题 4：Checkpointer 与 memory.json 的边界

**面试问题：**

session 级记忆和长期记忆分别解决什么问题？`Checkpointer` 和 `memory.json` 的职责边界是什么？为什么不能只用其中一个？

**你当前的问题：**

- 大方向对，但“防止 LLM 遗忘”说得比较泛。
- 没有强调 Checkpointer 保存 LangGraph thread 状态，而不是用户画像。
- 没有主动带出纠错、强化、置信度、去重这些质量控制点。

**完整回答范例：**

我把记忆分成两层：session 级状态和长期记忆。

session 级主要由 LangGraph 的 `Checkpointer` 负责，它跟 `thread_id` 绑定，用来保存当前会话的运行状态，包括消息历史、图执行状态、工具调用过程等。它解决的是“当前会话如何连续执行、异常后如何恢复、下一轮如何接着上一轮上下文走”的问题。

长期记忆由 `memory.json` 负责，它不是保存完整聊天记录，而是保存经过筛选和总结后的用户画像、偏好、长期背景、事实和历史摘要。比如用户偏好的回答风格、长期目标、项目背景、被用户纠正过的事实等。它会在构建 system prompt 时按 `agent_name` 注入，让 Agent 跨会话理解用户。

两者不能互相替代。只用 Checkpointer，历史会越来越长，token 成本高，而且里面有大量工具调用和临时上下文，不适合作为长期用户画像。只用 `memory.json`，又会丢掉当前会话的精确上下文和执行状态，比如刚才工具调用到哪一步、当前 thread 的消息链是什么。

所以我的设计是：Checkpointer 负责短期、精确、可恢复的会话状态；`memory.json` 负责长期、压缩、可注入的用户和 Agent 记忆。并且长期记忆更新时会过滤工具消息和临时上传文件，只保留用户输入和最终回答，同时通过纠错信号、正向强化信号、confidence 阈值和内容去重来控制写入质量。

**重点代码：**

- `backend/agents/checkpointer/provider.py`
- `backend/agents/checkpointer/async_provider.py`
- `backend/agents/memory/storage.py`
- `backend/agents/memory/updater.py`
- `backend/agents/memory/queue.py`
- `backend/agents/middlewares/memory_middleware.py`

## 问题 5：上传文件为什么不能写入长期记忆

**面试问题：**

如果用户只是临时上传了一个文件，比如“帮我看一下这个 PDF”，为什么这个上传事件不应该被写入长期记忆？你的项目里是怎么避免这类临时上下文污染 `memory.json` 的？

**你当前的问题：**

- 你说“还没有实现”，但代码里其实已经实现了一部分。
- human-in-the-loop approval 可以作为未来优化方案，但不能把已有实现说成没有。

**完整回答范例：**

临时上传文件不应该直接写入长期记忆，因为它属于当前 session 的上下文。比如用户上传一个 PDF，只能说明这轮对话需要处理这个文件，并不代表用户长期偏好或稳定事实。如果把“用户上传了某个文件”写入 `memory.json`，下一次会话里 Agent 可能会误以为这个文件仍然可访问，从而去查找已经不存在的上传路径，造成错误行为。

我在项目里做了两层过滤。第一层是在 `MemoryMiddleware` 里，写入记忆前会过滤消息，只保留用户输入和最终 assistant 回复，跳过 tool message 和带 tool_calls 的中间 AI message。同时如果 human message 中包含 `<uploaded_files>` 块，会把这个临时上传块剥离，只保留用户真正的问题。

第二层是在 `MemoryUpdater` 保存前调用 `_strip_upload_mentions_from_memory()`，会从 summary 和 facts 里再次移除描述上传事件的句子，比如 upload file、attachments、`/mnt/user-data/uploads/` 等，避免 LLM 总结时把临时上传事件误写进长期记忆。

未来可以加 human-in-the-loop approval，用于高风险记忆写入，比如用户身份、长期偏好、纠错事实等。但临时上传文件这类污染，最好先通过规则过滤自动兜底。

**重点代码：**

- `backend/agents/middlewares/memory_middleware.py`
- `backend/agents/memory/updater.py`

## 问题 6：MemoryUpdateQueue 与 debounce

**面试问题：**

为什么记忆更新要做队列和 debounce？如果每一轮对话结束后都立刻调用 LLM 总结并写入 `memory.json`，会有什么问题？

**你当前的问题：**

- 你答到了成本和并发压力，但没有说清楚 debounce 的核心价值：合并同一 thread 的最新上下文。

**完整回答范例：**

记忆更新我没有放在主对话链路里同步执行，而是通过队列和 debounce 异步处理，主要是为了降低成本、减少延迟，并提升写入稳定性。

用户连续对话时，每一轮都立刻调用 LLM 总结会有几个问题：第一，会阻塞主流程，用户每发一轮都要额外等一次记忆总结；第二，LLM 调用成本和 rate limit 压力会明显增加；第三，短时间内多个更新都基于旧的 `memory.json` 读写，可能出现后完成的旧更新覆盖新更新，也就是 lost update。

debounce 的作用是把一段时间内同一个 `thread_id` 的记忆更新合并，只保留最新的对话上下文，并合并纠错和正向强化信号。这样可以避免每个碎片轮次都写入记忆，让记忆更新更像“稳定沉淀”，而不是实时日志。

同时队列会把不同 thread 的更新批量处理，处理期间用锁保护队列状态，避免并发读写队列导致状态混乱。这样主对话可以快速返回，长期记忆在后台慢慢更新。

**重点代码：**

- `backend/agents/memory/queue.py`
- `backend/agents/memory/updater.py`

## 问题 7：本地安全沙箱与路径映射

**面试问题：**

为什么 AgentFlow 需要一个本地安全沙箱？在你的实现里，`/mnt/user-data/workspace` 这样的虚拟路径是怎么映射到 Windows 本地目录的？同时你做了哪些措施防止 Agent 越权读写用户机器上的文件？

**你当前的问题：**

- 你只讲了“防止危险操作”这个目的，没有讲实现。
- 不熟悉虚拟路径到真实路径的映射。
- 没讲清权限边界。

**完整回答范例：**

AgentFlow 需要本地安全沙箱，是因为 Agent 具备文件读写和命令执行能力。如果直接让模型操作宿主机路径，它可能误删文件、覆盖用户数据，或者执行高风险命令。所以我把 Agent 可见的文件系统限制在一套虚拟路径下，比如 `/mnt/user-data/workspace`、`/mnt/user-data/uploads`、`/mnt/user-data/outputs`。

路径映射由 `ThreadDataMiddleware` 和 `Paths` 统一管理。每个会话有自己的 `thread_id`，系统会为它生成独立目录：

```text
.deer-flow/threads/{thread_id}/user-data/workspace
.deer-flow/threads/{thread_id}/user-data/uploads
.deer-flow/threads/{thread_id}/user-data/outputs
```

Agent 在 prompt 和工具里看到的是虚拟路径 `/mnt/user-data/workspace`，真正执行文件操作时，沙箱工具会把这个虚拟路径解析到 Windows 本地的实际目录。这样 Agent 不需要知道真实 Windows 路径，也不能随便访问 `C:\Users\...` 下的其他文件。

防越权主要有几层：第一，路径解析时只允许 `/mnt/user-data` 下的 workspace、uploads、outputs 等受控目录；第二，会做路径合法性校验，防止 `..` 或绝对路径逃逸到工作区外；第三，对自定义挂载区分 read-only 和 read-write，像某些外部目录只能读不能写；第四，Host Bash 不是默认开放的，需要显式配置允许，并且执行前会做路径和权限检查。

所以沙箱的核心不是“完全不能执行”，而是把执行限制在当前 thread 的受控工作区里。即使 Agent 写文件或跑命令，影响范围也被限制在 `.deer-flow/threads/{thread_id}/...` 下面。

**重点代码：**

- `backend/agents/middlewares/thread_data_middleware.py`
- `backend/config/paths.py`
- `backend/sandbox/tools.py`
- `backend/sandbox/security.py`
- `backend/sandbox/local/local_sandbox.py`
- `backend/sandbox/local/local_sandbox_provider.py`

## 问题 8：路径穿越如何拦截

**面试问题：**

如果用户让 Agent 读取 `/mnt/user-data/workspace/../../../../Users/BAI/Desktop/private.txt`，或者让 bash 执行一个会写到工作区外的命令，你的沙箱应该怎么拦截？

**你当前的问题：**

- 你说到了用正则看到 `../` 就拒绝，但安全路径校验不能只靠字符串匹配。
- 没有讲 `Path.resolve()` 和 allowed roots 校验。

**完整回答范例：**

对这类路径，我会做两层校验。第一层是字符串级的路径穿越检测，把 `\` 统一成 `/`，检查路径 segment 里是否出现 `..`。比如 `/mnt/user-data/workspace/../../private.txt` 会直接被拒绝。

但只靠这个不够，所以第二层会把虚拟路径解析成真实 host 路径，再调用 `Path.resolve()` 得到规范化后的绝对路径，然后检查它是否仍然位于当前 thread 的允许根目录下，比如 workspace、uploads、outputs。只有 `resolved.relative_to(root)` 成功，才允许访问。否则说明它逃逸出了沙箱目录，直接抛 `PermissionError`。

在我的实现里，`validate_local_tool_path()` 先检查路径是否属于允许的虚拟路径族，比如 `/mnt/user-data/*`，并拒绝 `..`；然后 `_resolve_and_validate_user_data_path()` 会把 `/mnt/user-data/workspace/...` 映射到当前 thread 的 Windows 本地目录，再用 `_validate_resolved_user_data_path()` 确认最终路径还在允许目录内。

对 bash 会更谨慎。因为 bash 是任意命令字符串，很难像文件工具一样完全静态分析。所以本地 LocalSandbox 下 Host Bash 默认不开放，只有配置 `sandbox.allow_host_bash: true` 才允许。同时执行前会扫描命令里的绝对路径，要求用户数据路径必须走 `/mnt/user-data` 这类虚拟路径，遇到 `file://` 或不在允许前缀里的绝对路径就拒绝。

所以整体策略是：工具入口可以按配置开关控制，但真正的安全边界放在路径解析和权限校验里。最终判断标准不是用户传入的字符串看起来安全，而是规范化后的真实路径是否还在允许根目录内。

**重点代码：**

- `backend/sandbox/tools.py`
- `backend/sandbox/security.py`

## 问题 9：为什么使用 Middleware 架构

**面试问题：**

为什么你要把这些能力做成 Middleware，而不是直接写在主 Agent 逻辑里？在 AgentFlow 中，Middleware 链路大概是怎么组织的？

**你当前的问题：**

- “模块化、方便控制”方向对，但太抽象。
- 把 Summarization 和长期记忆混在一起了。
- ThreadData 不是存储 thread_id，而是根据 thread_id 注入路径。

**完整回答范例：**

我把这些能力做成 Middleware，是因为它们本质上不是业务工具，而是 Agent 运行时治理能力。比如上下文压缩、记忆更新、工具过滤、循环检测、错误处理、会话目录注入，这些都应该横切在 Agent 执行链路里。如果直接写在主 Agent 逻辑里，`make_lead_agent()` 会变得很重，而且不同能力之间耦合很高，不方便按配置启停和复用。

Middleware 的好处是可以按阶段拦截 Agent 生命周期，比如在 `before_agent` 初始化线程数据，在 `wrap_model_call` 修改模型请求，在 `wrap_tool_call` 拦截工具调用，在 `after_agent` 做异步记忆更新。这样每个模块只关心自己的治理点。

在我的项目里，`_build_middlewares()` 会根据运行时配置组装中间件链。比如先加入基础运行时中间件，包括 ThreadData、Sandbox、工具错误处理等；如果开启 summarization，就加入 `SummarizationMiddleware` 做上下文压缩；如果是 plan mode，就加入 `TodoMiddleware`；之后加入 `MemoryMiddleware`，在 Agent 执行结束后把过滤后的对话加入长期记忆更新队列；如果开启 tool_search，就加入 `DeferredToolFilterMiddleware`，在模型调用前过滤 deferred tool schema，并在工具调用时阻止未 promote 的工具；最后加入 `LoopDetectionMiddleware` 和 `ClarificationMiddleware` 做执行管控和澄清拦截。

举例来说，`ThreadDataMiddleware` 工作在 `before_agent` 阶段，根据当前 `thread_id` 注入 workspace、uploads、outputs 路径，实现会话文件隔离。`DeferredToolFilterMiddleware` 工作在 `wrap_model_call` 和 `wrap_tool_call`，控制工具 schema 是否暴露以及 deferred 工具能否被直接调用。`MemoryMiddleware` 工作在 `after_agent`，不阻塞主流程，把对话送入异步队列做长期记忆沉淀。

**重点代码：**

- `backend/agents/lead_agent/agent.py`
- `backend/agents/middlewares/thread_data_middleware.py`
- `backend/agents/middlewares/deferred_tool_filter_middleware.py`
- `backend/agents/middlewares/loop_detection_middleware.py`
- `backend/agents/middlewares/memory_middleware.py`

## 问题 10：LoopDetection 死循环检测

**面试问题：**

Agent 为什么会出现工具调用死循环？你的 LoopDetection 应该检测哪些模式？检测到之后应该怎么打断，才能既避免无限循环，又不误杀正常的多轮工具调用？

**你当前的问题：**

- 你方向很好，但“一个工具最多调用 3 次”不准确。
- 实际限制的是重复 tool-call pattern，而不是工具名。

**完整回答范例：**

Agent 出现工具调用死循环，通常是因为外部工具持续失败、结果不满足模型预期，或者模型没有意识到重复调用不会产生新信息。比如网络搜索失败、文件路径不存在、工具返回格式异常，模型可能不断用同一组参数重试，直到 LangGraph recursion limit 报错。

我的检测不是简单统计某个工具被调用几次，而是在 `after_model` 阶段检查最后一条 AIMessage 的 `tool_calls`，把工具名和关键参数规范化后生成稳定 hash。比如 `query`、`path`、`command` 这些关键字段会参与判断；`read_file` 还会按行号 bucket 做归一化，避免只差几行就绕过检测；`write_file` 这类内容敏感工具则会保留完整参数，避免误判。

然后我按 `thread_id` 维护一个滑动窗口，默认只看最近 20 次工具调用。如果同一个 hash 出现 3 次，就注入一条 warning，让模型停止重复调用并尝试总结当前结果；如果出现 5 次，就进入 hard stop：清空最后消息里的 `tool_calls`，并追加 forced-stop 文本，强制模型输出最终回答。

为了避免误杀正常任务，我没有限制“同一个工具最多调用几次”，而是限制“同一个工具以同一组关键参数反复调用”。这样一个 Agent 可以正常多次读不同文件、搜索不同关键词，但不能一直重复调用完全相同的失败操作。每个 thread 独立维护 loop history，并用 LRU 控制最多跟踪的线程数量。

**重点代码：**

- `backend/agents/middlewares/loop_detection_middleware.py`

## 问题 11：MCP 工具接入与工具治理

**面试问题：**

你的 AgentFlow 是如何接入 MCP 工具的？MCP 工具和内置工具、配置化工具是什么关系？运行时又是怎么根据 group、model、subagent_enabled、tool_search 这些条件决定最终给 Agent 哪些工具的？

**你当前的问题：**

- MCP 不熟练。
- “MCP 工具需要显式调用才能 promote”只在 `tool_search.enabled = true` 时成立。
- 没讲 MCP 初始化、缓存和配置变更重新加载。

**完整回答范例：**

AgentFlow 里工具最终都会统一成 LangChain 的 `BaseTool`，然后传给 `create_agent()`。但工具来源分三类：内置工具、配置化工具和 MCP 工具。

内置工具是框架固定提供的，比如 `present_file`、`ask_clarification`，如果开启 `subagent_enabled`，还会加入 `task_tool`。配置化工具来自 `config.yaml` 里的 `tools`，每个工具有 `name/group/use`，运行时通过 `resolve_variable(tool.use, BaseTool)` 动态加载。MCP 工具来自 extensions 配置里启用的 MCP server，系统会根据 server 的 transport 类型构建参数，比如 `stdio` 需要 command/args/env，`sse/http` 需要 url/headers，然后用 `MultiServerMCPClient` 拉取工具。

MCP 工具初始化时会先调用 `initialize_mcp_tools()`，把 enabled MCP servers 的工具加载并缓存。后续 `get_available_tools()` 里通过 `get_cached_mcp_tools()` 拿缓存，避免每次创建 Agent 都重新连接 MCP server。同时会检查 extensions 配置文件的修改时间，如果配置变了，就重置缓存重新加载。

最终工具选择是在 `get_available_tools()` 里完成的。首先根据 `groups` 过滤配置化工具，只加载当前 Agent 允许的工具组；如果本地 LocalSandbox 没有显式允许 host bash，会把 bash 类工具过滤掉；然后加入内置工具；如果 `subagent_enabled=true`，再加入 `task_tool`；如果开启 MCP，则加入缓存的 MCP tools。

还有一层是 `tool_search`。如果 `tool_search.enabled=false`，MCP 工具会直接进入 Agent 的 tools 列表并暴露 schema。若为 true，MCP 工具仍然进入执行层 tools 列表，但会被注册到 `DeferredToolRegistry`，同时加入 `tool_search` 内置工具。之后 `DeferredToolFilterMiddleware` 会在模型调用前把 MCP 工具 schema 过滤掉，只在 prompt 里列出名字；模型需要时通过 `tool_search` 搜索并 promote，对应工具下一轮才暴露完整 schema。

所以整体设计是：工具对象统一，来源多样；准入由配置和运行时参数控制；MCP 工具再通过缓存和延迟 schema 暴露降低启动和 prompt 成本。

**重点代码：**

- `backend/deer_flow_mcp/tools.py`
- `backend/deer_flow_mcp/client.py`
- `backend/deer_flow_mcp/cache.py`
- `backend/tools/tools.py`
- `backend/config/extensions_config.py`
- `backend/config/tool_config.py`

## 问题 12：项目整体架构

**面试问题：**

一次用户输入从进入系统到 Agent 返回结果，中间经过哪些核心模块？

**你当前的问题：**

- 回答较完整，但把“创建自定义 Agent”的前置流程和“一次用户输入执行链路”混在一起。
- Middleware 顺序表述不够准确。
- 漏了 Checkpointer。

**完整回答范例：**

AgentFlow 的整体链路可以分成配置层、运行时 Agent 层、工具层、中间件治理层和记忆持久化层。

首先用户会选择一个 `agent_name`，系统会为每个 Agent 维护独立配置目录，包括 `config.yaml`、`SOUL.md` 和 `memory.json`。同时我会维护 `agent_name -> thread_id` 的映射，同一个 Agent 使用稳定的 thread_id，这样它的会话状态和 workspace 可以持续复用。

当用户输入一轮消息时，系统会把当前 `agent_name`、`thread_id`、模型参数、plan mode、subagent 开关等放进 runtime config。然后调用 `make_lead_agent()` 创建运行时 Agent。这个过程中会读取 Agent 的配置，解析模型 profile，加载对应的 `SOUL.md` 和长期记忆，并通过 `apply_prompt_template()` 拼出最终 system prompt。

工具层由 `get_available_tools()` 统一加载。它会合并内置工具、配置化工具、MCP 工具和可选的 subagent 工具。配置化工具会按 group 过滤，LocalSandbox 下 host bash 默认会被关掉；MCP 工具会先在启动阶段缓存，如果开启 `tool_search`，MCP 工具 schema 不会一开始全部暴露，而是通过 `DeferredToolRegistry + tool_search` 按需 promote。

Agent 执行时会经过一组 Middleware 做治理。比如 `ThreadDataMiddleware` 根据 thread_id 注入 workspace/uploads/outputs 路径；`SummarizationMiddleware` 在上下文过长时压缩历史；`TodoMiddleware` 在 plan mode 下提供任务管理；`DeferredToolFilterMiddleware` 控制延迟工具 schema；`LoopDetectionMiddleware` 在模型输出 tool_calls 后检测重复调用；`ClarificationMiddleware` 处理需要澄清的场景。

Checkpointer 负责 session 级状态持久化，也就是同一个 thread 的消息历史和图执行状态。长期记忆则由 `MemoryMiddleware` 在 `after_agent` 阶段触发，它会过滤中间 tool 消息，只保留用户输入和最终回答，送入异步队列，最后总结更新到对应 Agent 的 `memory.json`。

最后 Agent 执行完成后，系统取最后一条 assistant message 返回给用户；同时后台记忆队列会异步沉淀长期记忆，不阻塞本轮响应。

**重点代码：**

- `main.py`
- `backend/agents/lead_agent/agent.py`
- `backend/agents/lead_agent/prompt.py`
- `backend/tools/tools.py`
- `backend/agents/checkpointer/async_provider.py`
- `backend/agents/middlewares/*`

## 问题 13：技术难点与权衡

**面试问题：**

这个项目里你觉得最有技术挑战的点是什么？你是怎么解决的？有没有做过什么权衡？

**建议选择方向：**

优先选 `tool_search` 延迟工具发现。它和当前大模型应用岗位最贴，也最能体现工程思考。

**完整回答范例：**

我觉得最有挑战的是 `tool_search` 延迟工具发现机制。背景是 AgentFlow 需要接入很多外部工具，尤其是 MCP 工具。如果把所有工具 schema 都直接暴露给模型，prompt token 成本会非常高，而且工具越多，模型选择工具时越容易被干扰。但如果完全不暴露工具，模型又不知道系统有哪些能力，也无法正确调用。

难点在于要同时满足三个目标：第一，降低 prompt 里的工具 schema 开销；第二，模型仍然知道有哪些工具可以被发现；第三，工具被发现后能够真实调用，而不是只返回一段说明。

我的方案是把工具分成执行层和模型可见层。MCP 工具仍然会进入 Agent 的 tools 列表，保证 ToolNode 执行层能找到它们；但同时把它们注册到 `DeferredToolRegistry`。模型调用前，`DeferredToolFilterMiddleware` 会把 deferred 工具 schema 从 `request.tools` 里过滤掉，只在 prompt 里列出工具名称。模型需要某个工具时，先调用 `tool_search`，`tool_search` 会返回匹配工具的完整 schema，并调用 `promote()` 把工具从 deferred 状态移除。下一轮模型绑定工具时，这个工具 schema 就会正常暴露，模型可以调用。

这个方案的权衡是：模型调用 deferred 工具通常需要多一轮 `tool_search`，所以交互步数可能增加；另外搜索质量也依赖工具名称和描述，如果工具描述不清楚，模型可能搜不到合适工具。为了解决这个问题，我支持了 `select:name` 精确选择、`+keyword` 强制关键词，以及普通 regex 搜索。

后续优化方向有几个：第一，可以给工具增加更结构化的 metadata，比如 category、capability、risk level，用更稳定的检索代替单纯关键词；第二，可以记录工具搜索和调用成功率，做工具排序；第三，可以在 prompt 里按场景动态推荐少量候选工具，而不是只列名字。

## 问题 14：Agent_name和Thread_id 区别
agent_name 表示“你现在使用哪个 Agent 身份”。
thread_id 表示“你现在在哪个会话线程里运行”

agent_name = 人格 / 配置 / 长期记忆归属
thread_id  = 会话 / 运行状态 / 文件工作区归属

### agent_name 负责什么
.deer-flow/agents/{agent_name}/
  config.yaml   # 模型、工具组、技能等配置
  SOUL.md       # Agent 人设、行为边界、沟通方式
  memory.json   # 该 Agent 的长期记忆

### thread_id 负责什么

.deer-flow/threads/{thread_id}/
  user-data/
    workspace/   # 临时工作区
    uploads/     # 用户上传文件
    outputs/     # Agent 输出文件
  acp-workspace/

### 二者怎么关联
当前实现里“每个 Agent 默认绑定一个稳定 thread_id”。这样切换回同一个 Agent 时，它能继续使用同一个会话状态和同一个 workspace。

agent_name -> thread_id
但一个 Agent 理论上可以有多个 thread



**重点代码：**

- `backend/tools/builtins/tool_search.py`
- `backend/agents/middlewares/deferred_tool_filter_middleware.py`
- `backend/tools/tools.py`

## 建议重点阅读顺序

按这个顺序看代码，最容易建立完整图景：

1. `main.py`
   - 看用户如何选择 Agent、如何设置 `agent_name` 和 `thread_id`。

2. `backend/agents/lead_agent/agent.py`
   - 看 `make_lead_agent()` 如何创建运行时 Agent。
   - 看 `_build_middlewares()` 如何组装中间件。

3. `backend/agents/lead_agent/prompt.py`
   - 看 SOUL、memory、skills、deferred tools 如何注入 prompt。

4. `backend/tools/tools.py`
   - 看内置工具、配置化工具、MCP 工具、subagent 工具如何合并。

5. `backend/tools/builtins/tool_search.py`
   - 看 `DeferredToolRegistry`、`ContextVar`、`tool_search()`、`promote()`。

6. `backend/agents/middlewares/deferred_tool_filter_middleware.py`
   - 看模型调用前如何过滤 schema，工具调用前如何兜底。

7. `backend/agents/memory/*`
   - 看长期记忆如何加载、过滤、排队、总结、保存。

8. `backend/sandbox/tools.py` 和 `backend/config/paths.py`
   - 看虚拟路径、真实路径、路径校验、安全边界。

9. `backend/agents/middlewares/loop_detection_middleware.py`
   - 看死循环检测的 hash、窗口、warning、hard stop。

10. `backend/deer_flow_mcp/*`
    - 看 MCP server 配置、工具加载、缓存和同步包装。

## 最需要背熟的 6 句话

1. `tool_search` 的核心是：MCP 工具仍在执行层，但 schema 被延迟暴露，模型需要时通过搜索 promote。

2. `ContextVar` 用来保证每个请求或 graph run 拥有独立的 deferred registry，避免并发污染。

3. `Checkpointer` 保存 session/thread 级精确状态，`memory.json` 保存跨会话长期摘要和事实。

4. 长期记忆不是日志，而是沉淀；所以要过滤工具消息、临时上传文件，并用置信度和去重控制写入质量。

5. 安全路径校验不能只看原始字符串，要看 `resolve()` 后的最终路径是否仍在允许根目录内。

6. LoopDetection 限制的是重复的 tool-call pattern，不是限制某个工具最多调用几次。

## 下一轮模拟面试建议

下一次建议重点练这 4 类问题：

1. **深挖 tool_search**
   - 为什么不是直接 RAG 搜工具？
   - tool_search 返回 schema 后为什么下一轮可调用？
   - 如果模型直接调用 deferred 工具怎么办？

2. **深挖 Memory**
   - 如何判断事实是否值得写入？
   - 如果 LLM 总结错了怎么办？
   - 多 Agent 记忆如何隔离？

3. **深挖 Sandbox**
   - path traversal 怎么防？
   - host bash 为什么默认禁用？
   - 本地沙箱和真正容器沙箱有什么区别？

4. **项目开场陈述**
   - 用 2 分钟讲清整体架构。
   - 用 1 分钟讲最大技术难点。
   - 用 1 分钟讲一个可改进点。

## 补充：DeerFlow、OpenClaw、Hermes Agent 对比

这一节用于回答面试官可能追问的开放问题：你怎么看 ByteDance DeerFlow？它和 OpenClaw、Hermes Agent 这类开源 Agent 项目有什么区别？可以用来证明你不是只会讲自己项目，也理解当前 Agent 工程化趋势。

### DeerFlow 的特点

DeerFlow 2.0 的定位是 **Super Agent Harness**，不是单纯聊天机器人，也不是单一 Deep Research Agent。它更像一个 Agent 运行底座，把模型、工具、技能、文件系统、沙箱、长期记忆、子 Agent 编排、Web UI 和 Gateway API 组合在一起，让 Agent 能执行较长时间、较复杂的任务。

它的核心特点主要有 7 个：

1. **从 Deep Research 进化成通用 Agent Harness**

   DeerFlow 早期偏深度研究，2.0 重写后变成通用长任务执行平台。它不只适合搜索和写报告，也适合生成网页、做幻灯片、处理文件、整理数据和执行复杂内容工作流。

2. **Lead Agent + Sub-Agents 架构**

   DeerFlow 有一个主 Agent 负责规划、拆解和综合，复杂任务可以动态创建多个子 Agent。每个子 Agent 有独立上下文、工具和终止条件，可以并行执行，最后把结构化结果交回主 Agent 汇总。

3. **Skills 驱动能力扩展**

   DeerFlow 通过 Skill 扩展能力。一个 Skill 通常由 `SKILL.md` 描述工作流、最佳实践、可用工具和参考资源。它的优势是按需加载技能，而不是一开始把所有能力都塞进上下文，适合长任务和多能力组合。

4. **沙箱和文件系统是核心能力**

   每个 thread 有独立的 `uploads`、`workspace`、`outputs`。Agent 可以读写文件、生成交付物、执行命令。生产环境推荐 Docker sandbox，本地模式下 host bash 默认不应开放，因为本地执行不是强安全隔离边界。

5. **重视 Context Engineering**

   DeerFlow 会隔离子 Agent 上下文、总结已完成子任务、把中间结果下沉到文件系统，并压缩不再关键的信息。它解决的是长任务中上下文爆炸、状态混乱和中间产物管理的问题。

6. **兼容 LangGraph / LangChain 生态**

   DeerFlow 后端 Agent runtime 基于 LangGraph，模型调用和工具抽象基于 LangChain。对于已经使用 LangGraph 的团队来说，它不是一个完全陌生的 Agent 框架，而是更像工程化包装和应用化扩展。

7. **Web UI + Gateway + API 化**

   DeerFlow 不是纯 CLI 工具，而是有 Next.js 前端、FastAPI Gateway、Nginx 入口、SSE 流式输出、文件上传和 artifact 管理的完整应用形态。因此它更适合作为团队内部 Agent 平台或私有 AI 工作台。

### DeerFlow 的设计理念

DeerFlow 的设计理念可以概括成一句话：

> 给 Agent 一套可靠的工作环境，而不是只给模型一堆工具。

它强调的不是单纯让模型更聪明，而是给 Agent 配齐真实完成任务所需的运行时基础设施：

- 可扩展的 Skill 体系
- 真实文件系统和产物目录
- 可控沙箱
- 长任务上下文治理
- 主 Agent 和子 Agent 协作
- 长期记忆
- Web/Gateway/API 部署形态
- MCP 和外部工具接入能力

所以 DeerFlow 的工程气质是 **平台化、工作流化、可部署化**。它关心的问题是：一个 Agent 要长期、稳定、可控地完成复杂任务，除了 LLM 本身，还需要哪些运行时能力。

### 和 OpenClaw 的区别

OpenClaw 更像 **local-first personal AI assistant**，也就是运行在用户自己设备上的个人 AI 助手。它强调通过 WhatsApp、Telegram、Slack、Discord、iMessage、微信、QQ 等消息渠道和用户交互，也支持 CLI、桌面端、移动端 companion app、语音和 Live Canvas。

| 维度 | DeerFlow | OpenClaw |
|---|---|---|
| 核心定位 | Super Agent Harness / 长任务工作台 | Local-first personal AI assistant |
| 主要入口 | Web UI、Gateway API、IM 渠道、Python client | 消息渠道、CLI、桌面/移动 companion app |
| 重点 | 深度任务、产物生成、多 Agent、沙箱、上下文工程 | 个人助理、常驻、跨 IM 渠道、设备和应用控制 |
| 架构气质 | LangGraph + FastAPI + Next.js，偏工程平台 | Gateway daemon + 多渠道连接，偏个人助手系统 |
| 多 Agent | 主 Agent 动态拆解子 Agent | 支持多 Agent 路由到隔离 workspace/session |
| 安全模型 | 线程级文件区，生产推荐 Docker sandbox | main session 更偏本机助手，其他 session 可 sandbox |
| 典型任务 | 调研报告、数据整理、网页/幻灯片/文件产物 | 收消息、调度、操作本地工具、跨平台个人自动化 |

一句话区别：

> OpenClaw 更像 AI 住进你的电脑和聊天软件里；DeerFlow 更像给 AI 一个可以完成复杂任务的工作台。

### 和 Hermes Agent 的区别

Hermes Agent 的定位更偏 **self-improving AI agent**，也就是自我改进型 Agent。它特别强调从经验中创建技能、在使用中改进技能、主动沉淀记忆、搜索过去会话、逐渐建立用户模型。

| 维度 | DeerFlow | Hermes Agent |
|---|---|---|
| 核心定位 | 通用 Super Agent Harness | 自我学习、自我改进 Agent |
| 最强卖点 | 多 Agent + 沙箱 + 产物工作流 + Web/Gateway | 学习循环、记忆、技能自生成/自改进 |
| 主要入口 | Web UI、API、IM、Python client | TUI/CLI、Messaging Gateway |
| 运行环境 | 本地、Docker、Kubernetes sandbox，偏服务化部署 | 本地、Docker、SSH、Singularity、Modal、Daytona、Vercel Sandbox 等 |
| 记忆 | 长期记忆，保存用户偏好和项目知识 | 更强调 agent-curated memory、会话搜索、用户建模 |
| 技能 | 按需加载、可扩展、可组合 | 可从复杂任务中自动创建并改进技能 |
| 典型任务 | 研究、报告、网页、幻灯片、文件处理 | 长期个人助手、自动化、周期任务、经验积累 |

一句话区别：

> Hermes 更关心 Agent 会不会越用越懂你、越用越会做事；DeerFlow 更关心 Agent 有没有一套稳定运行时去完成复杂任务。

### 三者总结

- **DeerFlow**：面向复杂任务和交付物的 Agent 工作台 / runtime。
- **OpenClaw**：面向个人日常自动化的本地常驻助手。
- **Hermes Agent**：面向长期陪伴、记忆沉淀和自我学习的 Agent。

如果目标是研究报告生成、企业内部 Agent 平台、多 Agent 工作流、文件产物生成，DeerFlow 更合适。

如果目标是接入微信、Slack、Telegram，让 AI 常驻在个人设备和聊天渠道里，OpenClaw 更贴近。

如果目标是研究 Agent 如何积累经验、自动沉淀技能、建立长期记忆和自我改进，Hermes Agent 更有代表性。

### 面试回答模板

如果面试官问“DeerFlow 有什么特点？和 OpenClaw、Hermes Agent 有什么区别？”，可以这样回答：

> 我理解 DeerFlow 2.0 的核心不是做一个聊天机器人，而是做一个 Super Agent Harness。它把模型、工具、Skill、文件系统、沙箱、长期记忆、子 Agent、Web UI 和 Gateway 组织成一个完整运行时，让 Agent 能执行长任务并产出真实文件。它的设计理念是给 Agent 一个可靠工作环境，而不是只给模型一堆工具。
>
> 和 OpenClaw 相比，OpenClaw 更像 local-first personal assistant，重点是接入各种 IM 和本机设备，成为个人常驻助手；DeerFlow 更像任务工作台，重点是复杂任务、产物生成、多 Agent 和 sandbox。
>
> 和 Hermes Agent 相比，Hermes 更强调 self-improving，也就是 Agent 从历史任务里自动沉淀记忆和技能，越用越会做事；DeerFlow 更强调运行时工程化，比如上下文治理、文件系统、沙箱、Gateway、Web UI 和多 Agent 编排。
>
> 所以如果我要做企业内部复杂任务 Agent 或研究报告/文件产物生成，我会优先看 DeerFlow；如果我要做个人设备上的常驻助手，我会看 OpenClaw；如果我要研究长期记忆和自我改进机制，我会看 Hermes Agent。
