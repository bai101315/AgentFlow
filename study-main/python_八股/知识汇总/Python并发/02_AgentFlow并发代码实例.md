# AgentFlow 中的并发：从真实代码理解进程、线程、协程与线程池

> 编号：K-CON-02
>
> 项目：`/home/pc/桌面/AgentFlow`
>
> 状态：正在学习
>
> 相关每日学习文件：`../../2026-08-20_Python并发专项.md`

## 1. AgentFlow 的真实并发结构

```text
AgentFlow 进程
  |
  +-- 同步调用线程
  |     +-- DeerFlowClient.stream()：同步生成器
  |     +-- agent.stream()：同步消费 LangGraph 事件
  |
  +-- 异步事件循环线程
  |     +-- get_mcp_tools()：异步加载 MCP 工具
  |     +-- ws_handler()：异步处理 WebSocket
  |     +-- broadcast()：异步广播观测事件
  |     +-- run_review()：异步执行后台 Review Agent
  |
  +-- 线程池
  |     +-- MCP 同步包装器
  |     +-- 已有事件循环时的 MCP 初始化
  |
  +-- ReviewScheduler 线程
        +-- 在线程中 asyncio.run(run_review(request))
```

代码证据：

- `backend/deer_flow_mcp/tools.py:18-22` 创建全局 MCP 同步工具线程池。
- `backend/deer_flow_mcp/tools.py:35-47` 在线程池中运行异步 MCP 协程。
- `backend/deer_flow_mcp/cache.py:99-122` 在已有事件循环时另起线程运行新的事件循环。
- `backend/agents/review_agent/runtime.py:375-429` 用线程启动后台 Review，并在线程中执行 `asyncio.run()`。
- `backend/client.py:456-462` 提供同步生成器 `stream()`；`backend/client.py:495-499` 明确说明它使用同步 `agent.stream()`，不是异步 `agent.astream()`。
- `backend/observability/web/app.py:36-57` 使用 `async def` 处理 WebSocket 和广播。

## 2. 进程：AgentFlow 的运行边界

在不使用多进程部署器的前提下，可以先把运行 AgentFlow 的 Python 服务理解为一个进程。进程拥有自己的 Python 解释器、虚拟地址空间、全局变量、MCP 缓存、Agent 对象、配置对象、线程和事件循环。

MCP 缓存中的全局变量见 `backend/deer_flow_mcp/cache.py:9-12`：

```python
_mcp_tools_cache: list[BaseTool] | None = None
_cache_initialized = False
_initialization_lock = asyncio.Lock()
```

这几个变量默认只属于当前进程。另一个进程不会自动共享这份 Python 列表，而会拥有自己的模块状态。

## 3. 线程：进程中的执行通道

`backend/agents/review_agent/runtime.py:375-387` 定义 `ReviewScheduler`，保存线程锁、并发信号量和线程集合：

```python
self._lock = threading.Lock()
self._semaphore = threading.BoundedSemaphore(max_concurrent)
self._threads: set[threading.Thread] = set()
```

`submit()` 在 `backend/agents/review_agent/runtime.py:404-429` 使用信号量限制并发 Review 数量，并创建线程：

```python
thread = threading.Thread(
    target=worker,
    name=f"background-review-{request.review_thread_id}",
)
```

`worker()` 在 `backend/agents/review_agent/runtime.py:411-418` 中执行：

```python
result = asyncio.run(run_review(request))
```

所以这里的关系是：

```text
AgentFlow 进程
  |
  +-- Review 线程
        |
        +-- asyncio.run()
              |
              +-- 该线程自己的事件循环
                    |
                    +-- 异步 run_review()
```

不是线程天然包含协程，而是代码主动在线程中创建事件循环后，事件循环才调度异步任务。

## 4. 协程与事件循环：AgentFlow 的异步路径

`backend/deer_flow_mcp/tools.py:57-58` 定义异步 MCP 工具加载：

```python
async def get_mcp_tools() -> list[BaseTool]:
```

它在 `backend/deer_flow_mcp/tools.py:88` 等待 OAuth，在 `tools.py:112-115` 异步加载工具：

```python
initial_oauth_headers = await get_initial_oauth_headers(extensions_config)
client = MultiServerMCPClient(...)
tools = await client.get_tools()
```

流程是：

```text
get_mcp_tools 协程
  |
  +-- await OAuth 网络操作
  |     -> 当前协程暂停
  |     -> 事件循环运行其他任务
  |
  +-- await client.get_tools()
        -> 等待 MCP 连接/工具发现
        -> 完成后从 await 后继续
```

WebSocket 也采用异步路径。`backend/observability/web/app.py:36-47` 中：

```python
@app.websocket("/ws")
async def ws_handler(ws: WebSocket) -> None:
    await ws.accept()
    await ws.receive_text()
```

`backend/observability/web/app.py:49-57` 的 `broadcast()` 使用 `await ws.send_text(payload)`。因此 WebSocket 连接主要由事件循环管理，不是每个连接创建一个线程。

## 5. AgentFlow 中同步调用异步代码的真实例子

`backend/deer_flow_mcp/tools.py:35-47` 的 `_make_sync_tool_wrapper()`：

```python
def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        future = _SYNC_TOOL_EXECUTOR.submit(
            asyncio.run,
            coro(*args, **kwargs),
        )
        return future.result()
    else:
        return asyncio.run(coro(*args, **kwargs))
```

逐步理解：

1. `tools.py:37` 检查当前线程是否已有运行中的事件循环。
2. 没有事件循环时，`tools.py:47` 直接 `asyncio.run()`。
3. 已有事件循环时，不能在同一线程再次 `asyncio.run()`，否则会遇到嵌套事件循环问题。
4. 因此 `tools.py:42-45` 把 `asyncio.run(coro(...))` 提交到全局线程池。
5. `future.result()` 会让同步调用等待线程池结果。

```text
当前线程已有事件循环
  |
  +-- 同步 MCP wrapper
        |
        +-- 提交到 ThreadPoolExecutor
              |
              +-- MCP 工作线程
                    |
                    +-- 新事件循环
                          |
                          +-- 执行 MCP 协程
```

这个项目实例说明：异步函数和同步调用可以混合，但必须明确哪个线程运行哪个事件循环，以及同步等待会阻塞哪一个线程。

## 6. AgentFlow 中已有事件循环时另起线程

`backend/deer_flow_mcp/cache.py:99-122` 的 `get_cached_mcp_tools()` 是同步函数。它需要确保异步初始化结束：

```python
loop = asyncio.get_event_loop()

if loop.is_running():
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(
            asyncio.run,
            initialize_mcp_tools(),
        )
        future.result()
else:
    loop.run_until_complete(initialize_mcp_tools())
```

当当前线程的事件循环已经运行时，代码创建线程池，在新线程中运行 `asyncio.run(initialize_mcp_tools())`。因此：

```text
当前线程：事件循环正在运行
  |
  +-- 不能在这里再次 asyncio.run()
  |
  +-- 新线程
        |
        +-- 新事件循环
              |
              +-- initialize_mcp_tools()
```

`future.result()` 是同步等待：调用 `get_cached_mcp_tools()` 的线程会等初始化完成。

## 7. AgentFlow 的同步流式路径

`backend/client.py:456-462` 的 `DeerFlowClient.stream()` 是同步生成器：

```python
def stream(...) -> Generator[StreamEvent, None, None]:
```

`backend/client.py:495-499` 明确说明：

```text
run_agent 是 async def 并使用 agent.astream()
本方法是 sync generator 并使用 agent.stream()
```

因此 AgentFlow 有两种不同消费路径：

```text
同步嵌入式调用：
DeerFlowClient.stream() -> agent.stream() -> for event in ...

异步网关/服务调用：
run_agent() -> agent.astream() -> StreamBridge/SSE
```

所以不能笼统说“AgentFlow 的线程里面都有协程”。同步 `stream()` 路径可以完全不需要 asyncio；异步网关路径才使用事件循环和协程。

## 8. 四个概念在 AgentFlow 中分别是什么

### 事件循环

代码证据：`backend/deer_flow_mcp/cache.py:103-117`、`backend/deer_flow_mcp/tools.py:37-47`。

事件循环是某个线程中的异步任务调度器，不是进程，不是一次请求。它负责在协程遇到 `await` 时暂停协程，并在等待的 I/O 就绪后恢复协程。

### 协程

代码证据：`backend/deer_flow_mcp/tools.py:57-58`、`backend/observability/web/app.py:36-37`。

协程是可被事件循环暂停和恢复的异步执行单元，适合等待 MCP、OAuth、WebSocket、SSE 等 I/O，不等于线程，也不等于并行。

### 线程池

代码证据：`backend/deer_flow_mcp/tools.py:18-19`、`tools.py:42-45`。

线程池是一组可复用的工作线程。AgentFlow 用它把同步工具调用包装成可在已有异步环境中执行的任务，并限制同步工具的并发数量。它不管理整个进程所有线程，也不负责调度普通协程。

### 子进程

本次读取的 AgentFlow 核心 MCP/Review 路径中，主要使用线程和事件循环，没有看到这些路径用 `ProcessPoolExecutor` 执行 Agent 任务。因此这里只做边界说明，不把通用概念误说成当前主路径：子进程是父进程创建的独立进程，拥有自己的地址空间、解释器和 GIL，适合纯 Python CPU 密集任务或隔离代码。当前 Review 实际使用的是 `runtime.py:411-414` 的线程中 `asyncio.run()`，不是子进程。

## 9. 最终记忆句

```text
AgentFlow 不是“一个进程里所有线程都有协程”。

同步 client.stream()：可以没有事件循环。
异步 get_mcp_tools()/WebSocket：运行在事件循环中的协程。
同步工具进入异步环境：交给 ThreadPoolExecutor。
后台 Review：ReviewScheduler 创建线程，线程里 asyncio.run()。
子进程：当前已读核心 Review/MCP 路径不是主方案，主要用于多核或隔离任务。
```

## 10. 项目代码复习题

1. `backend/deer_flow_mcp/tools.py:42-45` 为什么把 `asyncio.run()` 提交到线程池？
2. `backend/deer_flow_mcp/cache.py:105-113` 为什么已有事件循环时不能直接再次 `asyncio.run()`？
3. `backend/client.py:495-499` 的 `stream()` 和异步 `astream()` 有什么区别？
4. `backend/agents/review_agent/runtime.py:411-414` 中线程和事件循环是什么关系？
5. AgentFlow 当前已读路径中，哪些地方明确使用了子进程？如果没有读到证据，应回答“当前这条路径没有确认使用子进程”，而不是猜测。
