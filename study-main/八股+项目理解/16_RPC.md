RPC = 像调用本地函数一样，去调用另一个进程/服务器里的函数。

# RPC 和 普通函数
```
普通函数：
你的代码
  -> 调用本地函数
  -> 函数直接返回结果

RPC:
你的代码
  -> 把“我要调用哪个方法 + 参数”打包成消息
  -> 发给另一个进程/服务
  -> 对方执行
  -> 对方把结果打包发回来
  -> 你的代码拿到结果
```

比如：
```json
调用：
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
jsonrpc：协议版本
id：请求编号，用来匹配响应
method：要调用的方法名
params：参数

返回值：
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [...]
  }
}
```

# MCP_server

## MCP server 里面确实是不同函数体
例如：
```ts
this.server.tool(
    "get_daily_challenge",
    "Retrieves today's LeetCode Daily Challenge problem...",
    {},
    async () => {
        const data = await this.leetcodeService.fetchDailyChallenge();
        return {
            content: [
                {
                    type: "text",
                    text: JSON.stringify({
                        date: new Date().toISOString().split("T")[0],
                        problem: data
                    })
                }
            ]
        };
    }
);
工具名：get_daily_challenge
工具描述：Retrieves today's LeetCode Daily Challenge...
参数 schema：{}
函数体：async () => { ... }
```

## MCP server 怎么把函数注册进去
MCP server 启动时会形成一个内部工具表，大概像：
```
MCP server 内部工具表：
  get_daily_challenge -> async () => {...}
  get_problem -> async ({ titleSlug }) => {...}
  search_problems -> async ({ category, tags, ... }) => {...}
```

## MCP server 怎么开始接收调用

工具注册完以后，server 接到 stdio transport：

这个 MCP server 开始监听 stdin/stdout 上的 MCP 请求
```ts
const transport = new StdioServerTransport();
await server.connect(transport);
```

从此以后，如果外部发来：```调用工具 get_problem，参数 titleSlug="two-sum"```

MCP server 就会在自己的工具表里找到：```get_problem -> async ({ titleSlug }) => {...}```

## 项目拿不到函数体，该怎么执行
因为 LeetCode MCP server 是 TypeScript/Node 写的。
所以 Python 这边只能拿一个“代理函数”。

还是```langchain_mcp_adapters```包帮忙处理的，

```python
    async def call_tool(
        runtime: Annotated[object | None, InjectedToolArg()] = None,
        **arguments: dict[str, Any],
    ) -> tuple[ConvertedToolResult, MCPToolArtifact | None]:
        """Execute tool call with interceptor chain and return formatted result.

        Args:
            runtime: LangGraph tool runtime if available, otherwise None.
            **arguments: Tool arguments as keyword args.

        Returns:
            A tuple of (content, artifact) where:
            - content: string, list of strings/content blocks, ToolMessage, or Command
            - artifact: MCPToolArtifact with structured_content if present, else None
        """
        mcp_callbacks = (
            callbacks.to_mcp_format(
                context=CallbackContext(server_name=server_name, tool_name=tool.name)
            )
            if callbacks is not None
            else _MCPCallbacks()
        )

        # Create the innermost handler that actually executes the tool call
        async def execute_tool(request: MCPToolCallRequest) -> MCPToolCallResult:
            """Execute the actual MCP tool call with optional session creation.

            Args:
                request: Tool call request with name, args, headers, and context.

            Returns:
                MCPToolCallResult from MCP SDK.

            Raises:
                ValueError: If neither session nor connection provided.
                RuntimeError: If tool call returns None.
            """
            tool_name = request.name
            tool_args = request.args
            effective_connection = connection

            # If headers were modified, create a new connection with updated headers
            modified_headers = request.headers
            if modified_headers is not None and connection is not None:
                # Create a new connection config with updated headers
                updated_connection = dict(connection)
                if connection["transport"] in (
                    "sse",
                    "http",
                    "streamable_http",
                    "streamable-http",
                ):
                    existing_headers = connection.get("headers", {})
                    updated_connection["headers"] = {
                        **existing_headers,
                        **modified_headers,
                    }
                    effective_connection = updated_connection

            captured_exception = None

            if session is None:
                # If a session is not provided, we will create one on the fly
                if effective_connection is None:
                    msg = "Either session or connection must be provided"
                    raise ValueError(msg)

                async with create_session(
                    effective_connection, mcp_callbacks=mcp_callbacks
                ) as tool_session:
                    await tool_session.initialize()
                    try:
                        call_tool_result = await tool_session.call_tool(
                            tool_name,
                            tool_args,
                            progress_callback=mcp_callbacks.progress_callback,
                        )
                    except Exception as e:  # noqa: BLE001
                        # Capture exception to re-raise outside context manager
                        captured_exception = e

                # Re-raise the exception outside the context manager
                # This is necessary because the context manager may suppress exceptions
                # This change was introduced to work-around an issue in MCP SDK
                # that may suppress exceptions when the client disconnects.
                # If this is causing an issue, with your use case, please file an issue
                # on the langchain-mcp-adapters GitHub repo.
                if captured_exception is not None:
                    raise captured_exception
            else:
                call_tool_result = await session.call_tool(
                    tool_name,
                    tool_args,
                    progress_callback=mcp_callbacks.progress_callback,
                )

            return call_tool_result

        # Build and execute the interceptor chain
        handler = _build_interceptor_chain(execute_tool, tool_interceptors)
        request = MCPToolCallRequest(
            name=tool.name,
            args=arguments,
            server_name=server_name or "unknown",
            headers=None,
            runtime=runtime,
        )
        call_tool_result = await handler(request)

        return _convert_call_tool_result(call_tool_result)
```

这个代理函数里面真正发远程调用的是：
```python
call_tool_result = await tool_session.call_tool(
    tool_name,
    tool_args,
    progress_callback=...
)
```
你的 TS 代码不是 Python 执行的。TS/JS 代码是在 Node.js 进程里执行的。

```
1. Python 在 tools list 里找到 StructuredTool

2. StructuredTool 执行它的 coroutine; 也就是langchain_mcp_adapters/tools.py 里的 call_tool

3. call_tool 里面执行：
   await tool_session.call_tool("get_problem", {"titleSlug": "two-sum"})

4. MCP Python SDK 把这个调用变成 JSON-RPC 消息
5. stdio_client 把 JSON 消息写进 Node 子进程 stdin
6. Node 进程里的 MCP server 收到消息
7. Node MCP server 在自己注册的工具表里找到：
   "get_problem" -> async ({ titleSlug }) => {...}
8. Node 执行这个 TS/JS 函数体
9. 函数体调用 LeetCode API
10. Node 把结果写回 stdout
11. Python stdio_client 从 stdout 读到 JSON-RPC 响应
12. tool_session.call_tool 返回结果
13. call_tool 代理函数把结果转成 LangChain 工具结果

```

# Node介绍
Node 是一个能在电脑本地运行 JavaScript/TypeScript 编译后代码的运行环境。
```
Python 程序：python main.py
Node 程序：node index.js

```


# 总结
## 四层架构
```
第 1 层：模型工具调用 Tool Call
第 2 层：项目里的工具系统 LangChain Tool / BaseTool
第 3 层：协议 MCP / ACP，它们常用 RPC 风格通信
第 4 层：传输方式 stdio / HTTP / SSE
```

类似于，llm需要调用工具，
```
DeerFlow 工具系统 backend/tools/tools.py
  找到对应 BaseTool
  |
  |-- 如果是内置工具：直接执行 Python 函数
  |
  |-- 如果是 sandbox 工具：执行本地/沙箱命令
  |
  |-- 如果是 MCP 工具：通过 RPC 调远处 MCP server
  |
  |-- 如果是 ACP 工具：理论上调另一个 Agent，但你项目里目前没接通
```

样例：
```
2. LLM 输出 tool call：
   调用 leetcode_search_problem

3. DeerFlow 在工具列表里找到这个工具

4. 这个工具其实是 MCP 工具，不是本地 Python 函数

5. MultiServerMCPClient 通过 JSON-RPC 发消息：
   method = tools/call
   params = { name: "search_problem", arguments: {...} }

6. 消息通过 stdio 发给 leetcode-mcp-server 子进程

7. leetcode-mcp-server 收到 RPC 请求

8. 它去请求 LeetCode API

9. 它把结果通过 stdout 返回给 DeerFlow

10. DeerFlow 把工具结果交给 LLM
```

## 答疑：
可以通过mcp拿到函数体，但是大概率还要调用mcp_client的内容
```python
def search_problem_proxy(...):
    return mcp_client.call_tool(
        server="leetcode",
        tool="search_problem",
        args=...
    )
```

