# 渐进式披露

三层过滤，
```
全部注册的工具 (~200+)
        │
        ▼ 第一层：Toolset 白名单/黑名单
    只保留 platform_toolsets 指定的工具
        │
        ▼ 第二层：check_fn 运行时可用性检查
    过滤掉环境不满足的工具 (如没配 API key)
        │
        ▼ 第三层：动态 Schema 修正
    根据实际可用工具调整跨引用描述
        │
    最终发给 LLM 的 schema 列表 (~40-60个)
```

## Toolset 分组 — 拆大为小
所有工具必须属于某个 toolset（定义在 toolsets.py）。CLI 模式的入口在 config.yaml 的 platform_toolsets.cli：

```python
platform_toolsets:
    cli:
    - hermes-cli        # 这是一个复合 toolset

"hermes-cli": {
    "tools": [],
    "includes": [
        "web", "browser", "terminal", "file", "code_execution",
        "vision", "image_gen", "tts", "skills", "memory",
        "session_search", "delegation", "cronjob", "clarify",
        "todo"
    ]
}

```

## 第二层：check_fn — 按环境自动隐身
这是最关键的一层。每个工具注册时带一个 check_fn：

```python
registry.register(
    name="browser_navigate",
    toolset="browser",
    check_fn=check_browser_requirements,  # ← 这里
    requires_env=["BROWSERBASE_API_KEY"],
    ...
)
```
check_fn 检查工具是否真的可用。不满足条件的工具根本不会出现在 schema 中，LLM 压根不知道它们存在。

典型例子：

| 工具 | check_fn 检查什么 |
|------|-------------------|
| browser_* | BROWSERBASE_API_KEY 是否设置 + playwright 是否安装 |
| ha_* (Home Assistant) | HASS_TOKEN 是否存在 |
| send_message | gateway 是否在运行 |
| kanban_* | HERMES_KANBAN_TASK 环境变量是否设置 |
| computer_use | macOS 上 cua-driver 是否安装 |
| image_generate | FAL_KEY 等图片生成 API key |
| all tools | agent.disabled_toolsets 中的工具会被黑名单过滤 |

check_fn 结果有 30 秒 TTL 缓存（_check_fn_cached），避免重复检查。

## 第三层：动态 Schema 修正 — 防止 LLM 幻觉

使工具被过滤了，某些工具的描述文字里可能引用了已被过滤掉的工具。比如 browser_navigate 的描述中有：
    
> "For simple information retrieval, prefer web_search or web_extract (faster, cheaper)."

如果 web_search 因为没配 API key 被过滤掉了，这段描述会让 LLM 尝试调用一个不存在的工具。所以 get_tool_definitions 里有描述文字动态修正：

python
第446-460行：如果 web 工具不可用，从 browser_navigate 描述中删除那段话
if not web_tools_available:
    desc = desc.replace("prefer web_search or web_extract...", "")

类似地，execute_code 的 schema 也会根据实际可用的 sandbox 工具动态重建（build_execute_code_schema）。


完整流程：
```
1. config.yaml → platform_toolsets.cli = ["hermes-cli"]
                ↓
2. resolve_toolset("hermes-cli")
    → 展开 includes：web, browser, terminal, file, ...
    → 展开每个子 toolset 的工具名
    → 得到 ~70 个工具名
                ↓
3. agent.disabled_toolsets ← 从 config.yaml 读取
    → 减去被禁用 toolset 中的工具
                ↓
4. registry.get_definitions(tools_to_include)
    → 对每个工具调用 check_fn()
    → browser_*：你有配置 BROWSERBASE_API_KEY 吗？
    → image_generate：FAL_KEY 存在吗？
    → ha_*：HASS_TOKEN 存在吗？
    → send_message：gateway 在运行吗？
    → kanban_*：HERMES_KANBAN_TASK 设置了？
    → 过滤后剩约 40-50 个
                ↓
5. 动态 Schema 修正
    → browser_navigate 描述去掉 web 工具引用
    → execute_code 只列出实际可用的 sandbox 工具
    → delegate_task 描述更新为当前 max_concurrent_children
                ↓
6. → 最终发送给 DeepSeek V4 的 tools 数组
```

# 执行过程，实现流程；
整个链路没有框架，纯 Python 原生实现。分五个阶段。

## 阶段一：工具自注册
每个 tools/xxx.py 文件在模块被导入时自动执行 registry.register()。例如 file_tools.py 末尾：
```python
registry.register(
    name="read_file",
    toolset="file",           # 归属哪个 toolset
    schema=READ_FILE_SCHEMA,  # OpenAI function-calling 格式
    handler=_handle_read_file, # 实际执行函数
    check_fn=_check_file_reqs, # 可用性检查
    emoji="📖",
    max_result_size_chars=100_000,
)
```

READ_FILE_SCHEMA 就是标准的 OpenAI function calling 格式：
```json
READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": "Read a text file with line numbers and pagination. Use this instead of cat/head/tail in terminal. Output format: 'LINE_NUM|CONTENT'. Suggests similar filenames if not found. Use offset and limit for large files. Reads exceeding ~100K characters are rejected; use offset and limit to read specific sections of large files. NOTE: Cannot read images or binary files — use vision_analyze for images.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read (absolute, relative, or ~/path)"},
            "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed, default: 1)", "default": 1, "minimum": 1},
            "limit": {"type": "integer", "description": "Maximum number of lines to read (default: 500, max: 2000)", "default": 500, "maximum": 2000}
        },
        "required": ["path"]
    }
}
```

注册时做的事情 (registry.py 第 234-305 行)：
    
```python
def register(self, name, toolset, schema, handler, check_fn=None, ...):
    self._tools[name] = ToolEntry(
        name=name,
        toolset=toolset,
        schema=schema,       # 原始 schema dict
        handler=handler,     # Callable
        check_fn=check_fn,   # Callable 或 None
        ...
    )
    self._generation += 1    # 递增版本号，让缓存失效
```

## 阶段二：模块自动发现
启动时 model_tools.py 调用 discover_builtin_tools()，扫描 tools/ 目录下所有 .py 文件：

```python
def discover_builtin_tools(tools_dir=None):
    tools_path = Path(tools_dir) or Path(file).resolve().parent
    module_names = [
        f"tools.{path.stem}"
        for path in sorted(tools_path.glob("*.py"))
        if path.name not in {"init.py", "registry.py", "mcp_tool.py"}
        and _module_registers_tools(path)   # AST 静态检查: 有这个文件调用了 registry.register()?
    ]
    for mod_name in module_names:
        importlib.import_module(mod_name)   # import 时自动触发 register()
```    
关键点：_module_registers_tools(path) 用 AST 静态分析，检查文件中是否有顶层 registry.register(...) 调用表达式。不是真执行，只是看源码结构。这样就避免导入那些不注册工具的工具辅助模块。

## 阶段三：Toolset 过滤 + check_fn 运行时检查
在 agent_init.py 中，代理初始化时调用：

在 agent_init.py 中，代理初始化时调用：
```yaml
→ platform_toolsets.cli = ["hermes-cli"]
    → resolve_toolset("hermes-cli") 
        → 展开成 70+ 个工具名字符串
            → registry.get_definitions(tools_to_include)
```

registry.get_definitions() 的核心逻辑 (registry.py 第 337-384 行)：
```python
def get_definitions(self, tool_names, quiet=False):
        result = []
        check_results = {}
        entries_by_name = {entry.name: entry for entry in self._snapshot_entries()}
        
        for name in sorted(tool_names):
            entry = entries_by_name.get(name)
            if not entry:
                continue
            
            # ★ 关键：如果 check_fn() 返回 False，这个工具直接跳过
            if entry.check_fn:
                if entry.check_fn not in check_results:
                    check_results[entry.check_fn] = _check_fn_cached(entry.check_fn)
                if not check_results[entry.check_fn]:
                    continue
            
            # 组装最终 schema
            schema_with_name = {**entry.schema, "name": entry.name}
            result.append({"type": "function", "function": schema_with_name})
        
        return result
```
_check_fn_cached() 有 30 秒 TTL 缓存，防止每次请求都重新探测 Docker、浏览器等外部状态

## 阶段四：组装成 API kwargs

过滤后的 tool schemas 存到 agent.tools。调用 LLM 时，build_api_kwargs() 直接把 agent.tools 传进去：
```python
agent/chat_completion_helpers.py 第 233-235 行
    
def build_api_kwargs(agent, api_messages):
    tools_for_api = agent.tools   # ← 这里！已经是过滤好的 [{type:"function", function:{...}}, ...]
    
    # ... 各种 provider 特殊处理 ...
    
    return transport.build_kwargs(
        model=agent.model,
        messages=api_messages,
        tools=tools_for_api,     # ← 原样传入
        ...
    )
```

对于你用的 DeepSeek（chat_completions 模式），transport 的 convert_tools() 是恒等函数——什么都不改：

```python
agent/transports/chat_completions.py 第 156-158 行

class ChatCompletionsTransport(ProviderTransport):
    def convert_tools(self, tools):
        return tools   # ← 直接返回，OpenAI 格式不需要转换


最终 build_kwargs 把 tools 塞进 api_kwargs["tools"]：

python
agent/transports/chat_completions.py 第 246-252 行

if tools:
    if is_moonshot_model(model):
        tools = sanitize_moonshot_tools(tools)   # Kimi 需要特殊处理
    api_kwargs["tools"] = tools
```

## 完整流程
```  
tools/file_tools.py
    │ registry.register(name="read_file", schema={...}, handler=fn, check_fn=fn)
    │ registry.register(name="write_file", ...)
    │ registry.register(name="patch", ...)
    │ registry.register(name="search_files", ...)
    ▼
tools/registry.py  (ToolRegistry 单例)
    │ self._tools["read_file"] = ToolEntry(...)
    │ self._tools["write_file"] = ToolEntry(...)
    │ ...
    ▼
model_tools.py
    │ discover_builtin_tools() → importlib 导入所有工具模块
    │ get_tool_definitions(enabled_toolsets=["hermes-cli"])
    │   → resolve_toolset → 70+ 工具名
    │   → registry.get_definitions(names) 
    │     → 每个工具: check_fn()? 
    │     → 不通过的直接跳过
    │   → 返回 ~45 个 {"type":"function","function":{...}}
    ▼
agent.tools = [...]  # 约 45 个 schema
    ▼
build_api_kwargs() 
    │ tools_for_api = agent.tools
    │ api_kwargs["tools"] = tools_for_api
    ▼
client.chat.completions.create(**api_kwargs)
    │ POST https://api.deepseek.com/v1/chat/completions
    │ Body: { "model": "deepseek-v4-pro", "messages": [...], "tools": [...] }
    ▼
DeepSeek API 返回
    │ response.choices[0].message.tool_calls
    │   [{id: "call_xxx", function: {name: "read_file", arguments: '{"path":"/tmp/x"}'}}]
    ▼
registry.dispatch("read_file", {"path": "/tmp/x"})
    │ entry = self._tools["read_file"]
    │ return entry.handler({"path": "/tmp/x"})  # → _handle_read_file()
    ▼
工具结果 JSON 字符串 → 追加到 messages → 下一轮循环
```



