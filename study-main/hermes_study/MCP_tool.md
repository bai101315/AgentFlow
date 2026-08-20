Hermes 对 MCP 工具的管控不只一层，一共有四个层面。

# 配置级——include / exclude 白名单黑名单

在 config.yaml 中可以直接精确控制每个 MCP 服务器暴露哪些工具：
```yaml
mcp_servers:
      github:
        command: "npx"
        args: ["-y", "@modelcontextprotocol/server-github"]
        env:
          GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_..."
        tools:
          include:                          # 白名单：只暴露这些
            - "search_repositories"
            - "get_file_contents"
            - "create_issue"
          # exclude:                        # 或者黑名单：全暴露，除了这些
          #  - "delete_repository"
          resources: false                  # 连资源列表工具也关闭
          prompts: false
```

 实现代码在 mcp_tool.py 第 3067-3082 行：
    
```python
tools_filter = config.get("tools") or {}
include_set = _normalize_name_filter(tools_filter.get("include"))
exclude_set = _normalize_name_filter(tools_filter.get("exclude"))

def _should_register(tool_name: str) -> bool:
    if include_set:
        return tool_name in include_set      # 有白名单 → 只注册白名单里的
    if exclude_set:
        return tool_name not in exclude_set  # 有黑名单 → 全注册除了黑名单里的
    return True                              # 都没配 → 全部注册
```
规则：include 优先于 exclude。两者都设了只看 include。

# 交互式——hermes mcp configure 图形界面
Hermes 提供了一个 curses（终端图形界面）交互式工具配置器。tools_config.py 第 2936 行：

```python
def _configure_mcp_tools_interactive(config):
    """Probe MCP servers, discover tools, show per-server curses checklist."""
    
    # 1. 连接到所有已配置的 MCP 服务器
    server_tools = probe_mcp_server_tools()
    
    # 2. 对每个服务器，显示工具列表 + 复选框
    for server_name, tools in server_tools.items():
        # GitHub MCP Server:
        #   [✓] create_issue   (Create an issue)
        #   [✓] search_repositories
        #   [ ] delete_repository  ← 你可以取消勾选
    
    # 3. 变更写回成 config.yaml 的 tools.exclude
```

hermes mcp configure

# 注册级——schema 规范性检查 + 碰撞保护 + 注入扫描
即使配置层允许，注册时还有三道检查。mcp_tool.py 第 3084-3103 行:

```python
for mcp_tool in server._tools:
    # 检查 1：注入扫描
    _scan_mcp_description(name, mcp_tool.name, mcp_tool.description or "")

    # 检查 2：schema 规范化
    schema = _convert_mcp_schema(name, mcp_tool)
    tool_name_prefixed = schema["name"]
    # 名字变成 "mcp_github_search_repositories"

    # 检查 3：碰撞保护
    existing_toolset = registry.get_toolset_for_tool(tool_name_prefixed)
    if existing_toolset and not existing_toolset.startswith("mcp-"):
        logger.warning("collides with built-in tool — skipping")
        continue  # ← MCP 工具名不能覆盖 Hermes 内置工具

    registry.register(
        name=tool_name_prefixed,
        toolset=f"mcp-{name}",           # toolset = "mcp-github"
        schema=schema,
        handler=_make_tool_handler(name, mcp_tool.name, server.tool_timeout),
        check_fn=_make_check_fn(name),   # 检查服务器是否还在线
    )
```

每个 MCP 工具注册到独立的 toolset，例如 GitHub MCP 服务器里的所有工具都属于 mcp-github 这个 toolset。这意味着你可以把整个 MCP 服务器作为一个 toolset 来启用/禁用：
hermes tools disable mcp-github    # 一键关闭所有 GitHub MCP 工具
hermes tools enable mcp-github     # 一键恢复

# 运行时——服务端能力过滤 + 安全扫描

prompt 注入扫描：_scan_mcp_description() 检查 MCP 工具的描述文字中是否包含提示注入模式（如 "ignore previous instructions"）——因为 MCP 服务器的描述是外部不可控的，可能被恶意构造。

能力自检：只注册服务器实际支持的 utility 工具。如果 MCP 服务器没有 resources 能力，就不会注册 list_resources 和 read_resource 工具。mcp_tool.py 第 2959-2964 行：

```python
_UTILITY_CAPABILITY_ATTRS = {
    "list_resources": "resources",    # 必须有 resources 能力
    "read_resource":  "resources",
    "list_prompts":   "prompts",      # 必须有 prompts 能力
    "get_prompt":     "prompts",
}
```

还多了一个维度：supports_parallel_tool_calls
每个 MCP 服务器可以声明自己支不支持并行调用：
```yaml
mcp_servers:
    github:
    supports_parallel_tool_calls: true   # 可以同时调多个 GitHub 工具
    filesystem:
    # 不设 = 默认串行
```

