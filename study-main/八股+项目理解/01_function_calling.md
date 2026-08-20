# 什么是 Function Calling ？原理是什么？

简要回答：
```
Function Calling我的理解是这样一套机制:开发者用JSON schema把工具描述好传给模型，模型判断需要调工具的时候不输出自然语言，而是直接输出一段结构化的tool.calls JSON，告诉你「我要调哪个函数、参数是什么」，你的代码拿到这段JSON去真正执行，把结果塞回对话，模型再生成最终答案。

整个流程本质上是两轮对话:第一轮模型说「我需要调这个工具」，你去执行，第二轮模型拿到执行结果说「答案是这个」。

我觉得最核心的设计是，模型全程只做决策，执行的事情一律由宿主代码完成，职责分得很清楚。
```

# 输入：JSON
由 LangChain 的 ```@tool(..., parse_docstring=True)``` 自动从函数签名、类型注解、docstring 生成。
会生成类似的OpenAI function，

```json
{
  "name": "web_search",
  "description": "Search the web.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "The query to search for."
      }
    },
    "required": ["query"]
  }
}
```

runtime: ToolRuntime[...] 是注入参数，不是模型要填写的参数。

```python
@tool("web_search", parse_docstring=True)
def web_search_tool(query: str) -> str:
    """Search the web.

    Args:
        query: The query to search for.
    """
```
会根据函数名和注释进行一一对应，进行填写并转换为json格式；runtime、tool_call_id 是注入参数，不要求模型填写。

# schema 如何交给模型

```create_agent()``` 内部会在模型调用前做类似 model.bind_tools(tools) 的事，把工具 schema 传给支持 tool calling 的模型，例如 OpenAI-compatible ChatOpenAI。

create_agent() 内部会在模型调用时把 tools 转成当前模型 provider 支持的工具格式，然后绑定到模型上。对 ChatOpenAI / OpenAI-compatible 模型，本质上就是转成 OpenAI function/tool schema，


create_agent() 全链路 Schema 传递拆解:
```
1, schema 标准化转换（create_agent 初始化阶段）
你传入的 tools 可以是任意格式：Python 函数、BaseTool 子类、Pydantic 模型、甚至是手写的字典。create_agent() 内部第一件事，就是做跨模型 Provider 的格式统一转换：

2, model.bind_tools() 的本质（绑定调用钩子，而非修改模型）

它的核心逻辑（LangChain 源码级）是：
1, 接收第一步转换好的标准化 Tool Schema
2, 生成一个新的 RunnableBinding 可运行对象（LangChain 核心的可执行单元）
3, 给这个新对象注入一个调用时默认参数：tools=转换好的Schema
4, 返回这个新的可运行对象，原始模型实例不受任何影响


3. Schema 真正交给模型（每次推理调用阶段）
Schema 真正被发送给大模型，只有一个时机：Agent 执行链走到模型调用节点、发起 API 请求的那一刻。
完整时序：

1. 用户输入触发 Agent 执行
2. Agent 完成上下文组装、历史消息拼接、工具权限校验等前置逻辑
3. 调用第二步生成的、绑定了 tools 的 Runnable 模型实例
4. Runnable 自动把提前绑定的 Schema 合并到本次请求的参数中
5. 把 messages + tools schema + 其他参数 一起发给大模型 API
大模型根据本次传入的 Schema，生成符合格式要求的工具调用或回答

```

```json
{
  "type": "function",
  "function": {
    "name": "web_search",
    "description": "Search the web.",
    "parameters": {...}
  }
}
```
之所以能自动识别，是因为工具都实现了 LangChain 的 BaseTool 接口，模型也是 LangChain 的 BaseChatModel，中间协议统一。

如果不是Langchain的结构，
- 1，将函数包装为langchain；
- 2，绕过 LangChain，自己按目标模型 API 的格式传 tools/schema，并自己解析 tool_calls、执行函数、追加 tool result message


# 模型输出 tool call 长什么样

模式会返回一个AIMessage，其中包含 tool_calls；可能是：
```json
{
  "type": "ai",
  "content": "",
  "tool_calls": [
    {
      "id": "call_search_1",
      "name": "web_search",
      "args": {
        "query": "LangGraph create_agent tool calling"
      }
    }
  ]
}
```
这个 id 非常重要，因为后面的 ToolMessage 必须带同一个 tool_call_id，这样模型 API 才知道“这个工具结果对应哪次工具调用”。

# 工具如何执行

create_agent() 内部的 LangGraph ToolNode 会根据 tool_calls[i].name 找到同名工具，然后把 args 传进去。

tools node 内部持有一个工具注册表，大致类似：

```python
{
    "web_search": web_search_tool,
    "read_file": read_file_tool,
    "task": task_tool,
}
```
当模式输出时，ToolNode 就按 name 查表，找到 read_file_tool，把 args 传进去执行，然后生成对应 ToolMessage。


工具多主要影响两件事：

```text
模型上下文变大：因为 schema 要传给模型
ToolNode 注册表变大：查找成本很小，通常不是瓶颈
```

这也是项目为什么设计了 tool_search：MCP 工具很多时，不把所有 schema 一次性塞给模型，而是先隐藏，只暴露一个搜索工具，需要时再提升某些工具 schema。

找到之后，会自动调用执行的函数，调用函数进行返回，比如```return json.dumps(normalized_results, indent=2, ensure_ascii=False)```, 再比如 read_file_tool 会读取文件并返回文本，bash_tool 会执行命令并返回 stdout/stderr 摘要，task_tool 会启动子 Agent 并最终返回：


# 返回ToolMessage

工具执行完后，LangGraph 会把工具结果包装成 ToolMessage，塞回消息历史。典型结构是：

```json
{
  "type": "tool",
  "name": "web_search",
  "tool_call_id": "call_search_1",
  "content": "[{\"title\":\"...\",\"url\":\"...\"}]"
}
```
所以从外部观察，一轮工具调用通常是：
```
HumanMessage: 用户问题
AIMessage: content="", tool_calls=[...]
ToolMessage: 工具返回结果
AIMessage: 基于工具结果生成最终回答
```

# 项目
## 工具分类

三类工具：config.yaml配置工具，内置工具```BUILTIN_TOOLS = [present_file_tool, ask_clarification_tool,] ```, MCP工具

核心链路：
```text
config.yaml / 内置工具
  -> get_available_tools()
  -> LangChain BaseTool / StructuredTool
  -> create_agent(model, tools, middleware, system_prompt)
  -> 模型收到工具 JSON schema
  -> 模型输出 AIMessage.tool_calls
  -> LangGraph ToolNode 执行对应工具
  -> 生成 ToolMessage
  -> ToolMessage 回到消息历史
  -> 模型再次推理并输出最终回答
```

### config.yaml配置工具
1. 在config中进行配置，比如：
    ```
    name: tavily_search
    group: web
    use: community.tavily.tools:web_search_tool
    ```
2. 执行时，会读取config，根据路径得到 tool.use：配置中定义的工具实现路径（如 ```deerflow.tools.web_search:WebSearchTool```）;
3. resolve_variable：反射加载工具类并实例化为 BaseTool 对象，完成「配置→工具实例」的转换； 根据tool.use将所有工具变为 ``` resolve_variable(tool.use, BaseTool)```

### 内置工具和MCP工具？？
```python
BUILTIN_TOOLS = [
    present_file_tool,
    ask_clarification_tool,
]
```

```present_file_tool、task_tool、tool_search``` 都是通过 @tool 装饰器创建的 StructuredTool，而 StructuredTool 是 BaseTool 的一种;MCP 工具通过 langchain-mcp-adapters 加载后，也已经是 BaseTool 兼容对象。
本项目一般不需要再手动转换。只有配置工具需要进行转换

## 工具失败时怎么处理
中间件主要有两个，模型调用失败和工具执行失败
- LLMErrorHandlingMiddleware：兜底模型调用失败
- ToolErrorHandlingMiddleware：兜底工具执行失败

### ToolErrorHandlingMiddleware：工具失败兜底

```执行流程
LLM 发起 tool_call
  ↓
LangGraph 准备执行工具
  ↓
ToolErrorHandlingMiddleware.wrap_tool_call 包住 handler
  ↓
调用 handler(request)
  ↓
如果工具成功，直接返回 ToolMessage 或 Command
  ↓
如果工具抛异常，记录日志并重试
  ↓
重试仍失败，构造 status="error" 的 ToolMessage 返回
  ↓
agent loop 继续，LLM 看到工具失败消息后再决定如何回答
```

会进行重试，如果是Langgraph的的信号，raise，保留 LangGraph 控制流信号（中断/暂停/恢复）；否则在log里打印；如果>max_attempts,就会返回失败信息
```
ToolMessage(
    content="Tool call failed: Tool 'xxx' failed after 2 attempt(s) ...",
    tool_call_id=...,
    name=tool_name,
    status="error",
)

```

### LLMErrorHandlingMiddleware：模型失败兜底
它处理的不是工具异常，而是模型 API 失败, ```def wrap_model_call(...)```，例如：
超时
连接错误
429 rate limit
500/502/503/504
server busy
quota 不足
auth 错误

1, 保留 LangGraph 控制流信号（中断/暂停/恢复）
2，分类错误信息，延迟时间，进行重试
3，不可重试或重试耗尽：返回 AIMessage 给用户;所以模型失败时，也不会直接把 Python 异常冒到用户侧，而是尽量变成一条可展示的 assistant 消息。

还有一个 dangling_tool_call_middleware.py (line 1)，用于修复“AIMessage 里有 tool_calls，但历史里缺少对应 ToolMessage”的情况。它会补一个：

```[Tool call was interrupted and did not return a result.]```

## deferred tool / tool_search 流程

MCP 工具可能很多，全部 schema 都塞给模型会浪费上下文。所以项目用 tool_search.py (line 1) 延迟暴露工具

```
MCP tools 初始化
  -> 注册到 DeferredToolRegistry
  -> DeferredToolFilterMiddleware 从 request.tools 中隐藏这些工具 schema
  -> 模型只能看到 tool_search
  -> 模型调用 tool_search("select:某工具")
  -> tool_search 返回该工具完整 OpenAI function schema
  -> registry.promote()
  -> 下一轮模型调用时该工具 schema 不再被过滤
  -> 模型才能正式调用这个 MCP 工具
```

tool_search 返回 schema 的代码是：

```python
tool_defs = [convert_to_openai_function(t) for t in matched_tools[:MAX_RESULTS]]
return json.dumps(tool_defs, indent=2, ensure_ascii=False)
```

所以模型第一次调用 tool_search 看到的是“工具定义”，不是目标工具的业务结果。

## 一些疑问？

get_available_tools 实际返回：BaseTool 兼容对象，不是字符串，也不是 schema。它们里面有 name、description、args_schema、执行函数等信息。

create_agent() 需要拿到这些完整对象，原因有两个：一是给模型绑定 schema，二是 ToolNode 真正执行工具时要能通过 name 找到对应函数。

设计：
```
tools=完整工具对象列表
  -> ToolNode 持有所有工具，负责执行

system_prompt=<available-deferred-tools>
  -> 只告诉模型有哪些延迟工具名字

DeferredToolFilterMiddleware
  -> 在每次模型调用前，从 request.tools 里过滤掉仍处于 deferred 状态的 MCP 工具
  -> 所以模型暂时看不到这些工具的 schema

tool_search
  -> 模型调用它拿到某些延迟工具的完整 schema
  -> registry.promote()
  -> 下一轮模型调用时，这些工具不再被过滤
```

真正发送给模型的 schema 是在 create_agent() ***内部模型调用阶段***绑定的，项目代码没有显式写 model.bind_tools(...)，因为 LangChain agent 框架替你做了。你如果想观察“过滤后本轮到底给模型哪些工具”，可以在 deferred_tool_filter_middleware.py (line 35) 的 _filter_tools() 打日志：

schema观察：
最直接可以在代码里用 LangChain 的转换函数看：
```python
from langchain_core.utils.function_calling import convert_to_openai_tool
from tools import get_available_tools

for tool in get_available_tools():
    print(convert_to_openai_tool(tool))
```


```python
logger.info("tools before filter: %s", [t.name for t in request.tools])
logger.info("tools after filter: %s", [t.name for t in active_tools])
```


为什么 system_prompt 不传正常工具？
因为正常工具的 schema 已经通过模型的 tool binding 机制传给模型了，不需要再用自然语言重复一遍。重复放进 prompt 会浪费 tokens，还可能让模型混淆“自然语言说明”和“真实可调用 schema”。

延迟工具不同：**它们的 schema 被中间件刻意从 request.tools 过滤掉了**，模型完全不知道它们存在，所以才需要在 prompt 里只列名字，让模型知道可以先调用 tool_search 获取 schema。



## create_agent

返回值： CompiledStateGraph， 也就是一个已经编译好的 LangGraph 状态图。

### 创建阶段
1. 标准化模型。
   如果 model 是字符串，就用 init_chat_model() 初始化；你项目传进去的是 create_chat_model(...) 返回的 BaseChatModel 实例。
2. 标准化工具。
   tools=get_available_tools(...) 传进去的是 BaseTool / callable / dict 列表。LangChain 会把普通 callable 转成 BaseTool，并把工具注册到一个 ToolNode 里。
3. 创建一个统一的 ToolNode。
4. 收集中间件。
5. 构建 LangGraph。
   ```
   START
  -> before_agent / before_model middleware
  -> model
  -> after_model middleware
  -> tools
  -> model
  -> ...
  -> END
   ```
6. 编译图。



