import logging

from langchain.tools import BaseTool

from config import get_app_config
from reflection import resolve_variable
from sandbox.security import is_host_bash_allowed
# from tools.builtins import ask_clarification_tool, present_file_tool, task_tool, view_image_tool

from tools.builtins import ask_clarification_tool, present_file_tool, task_tool
from tools.builtins import ask_clarification_tool, present_file_tool

from tools.builtins.tool_search import reset_deferred_registry

logger = logging.getLogger(__name__)

BUILTIN_TOOLS = [
    present_file_tool,
    ask_clarification_tool,
]

SUBAGENT_TOOLS = [
    task_tool,
    # task_status_tool is no longer exposed to LLM (backend handles polling internally),
]

def _is_host_bash_tool(tool: object) -> bool:
    """Return True if the tool config represents a host-bash execution surface."""
    group = getattr(tool, "group", None)
    use = getattr(tool, "use", None)
    if group == "bash":
        return True
    if use == "sandbox.tools:bash_tool":
        return True
    return False

def get_available_tools(
    groups: list[str] | None = None,
    include_mcp: bool = True,
    model_name: str | None = None,
    subagent_enabled: bool = False,
) -> list[BaseTool]:
    
    """Get all available tools from config.

    Note: MCP tools should be initialized at application startup using
    `initialize_mcp_tools()` from deerflow.mcp module.

    Args:
        groups: Optional list of tool groups to filter by.
        include_mcp: Whether to include tools from MCP servers (default: True).
        model_name: Optional model name to determine if vision tools should be included.
        subagent_enabled: Whether to include subagent tools (task, task_status).

    Returns:
        List of available tools.
    """

    # 读取 DeerFlow 全局配置（config.yaml），其中 tools 节点定义了所有「配置化工具」（非内置工具）
    config = get_app_config()
    tool_configs = [tool for tool in config.tools if groups is None or tool.group in groups]

    # Do not expose host bash by default when LocalSandboxProvider is active.
    # 核心安全机制：当 LocalSandboxProvider 激活时，默认隐藏 host_bash 工具（防止恶意执行主机命令）
    if not is_host_bash_allowed(config):
        tool_configs = [tool for tool in tool_configs if not _is_host_bash_tool(tool)]

    # tool.use：配置中定义的工具实现路径（如 deerflow.tools.web_search:WebSearchTool）
    # resolve_variable：反射加载工具类并实例化为 BaseTool 对象，完成「配置→工具实例」的转换
    # 根据config.yaml 加载工具deerflow.community.ddg_search.tools:web_search_tool,
    loaded_tools = [resolve_variable(tool.use, BaseTool) for tool in tool_configs]
    
    # loaded_tools:[StructuredTool(name='web_search', description='Search the web for information. Use this tool to find current information, news, articles, and facts from the internet.', args_schema=<class 'langchain_core.utils.pydantic.web_search'>, func=<function web_search_tool at 0x000002810E587E20>)]
    # print(f"loaded_tools:{loaded_tools}")

    # Conditionally add tools based on config
    # 内置基础的工具集
    builtin_tools = BUILTIN_TOOLS.copy()
    # print(f"builtin_tools: {builtin_tools}")

    skill_evolution_config = getattr(config, "skill_evolution", None)
    if getattr(skill_evolution_config, "enabled", False):
        # Lazy import: 好处 1：避免循环依赖。2：减少启动时间
        from tools.skill_manage_tool import skill_manage_tool
        builtin_tools.append(skill_manage_tool)
    
    # Add subagent tools only if enabled via runtime parameter
    # 子智能体工具（如 task_tool）默认不启用，因为它们需要更复杂的思维和规划能力，可能不适合所有模型或使用场景。
    # 通过 runtime 参数控制是否包含这些工具，可以让用户根据实际需求和模型能力灵活选择。
    if subagent_enabled:
        builtin_tools.extend(SUBAGENT_TOOLS)
        logger.info("Including subagent tools (task)")

    # If no model_name specified, use the first model (default)
    if model_name is None and config.models:
        model_name = config.models[0].name
    
    # Add view_image_tool only if the model supports vision
    # NOTE: 不考虑
    # model_config = config.get_model_config(model_name) if model_name else None
    # if model_config is not None and model_config.supports_vision:
    #     builtin_tools.append(view_image_tool)
    #     logger.info(f"Including view_image_tool for model '{model_name}' (supports_vision=True)")

    # Get cached MCP tools if enabled
    # NOTE: We use ExtensionsConfig.from_file() instead of config.extensions
    # to always read the latest configuration from disk. This ensures that changes
    # made through the Gateway API (which runs in a separate process) are immediately
    # reflected when loading MCP tools.
    # 使用ExtensionsConfig.from_file()确保读取最新的MCP
    
    mcp_tools = []
    # Reset deferred registry upfront to prevent stale state from previous calls
    # 预先重置延迟注册表，以防止先前调用导致状态过时
    reset_deferred_registry()
    if include_mcp:
        try:
            from config.extensions_config import ExtensionsConfig
            from deer_flow_mcp.cache import get_cached_mcp_tools

            # 从磁盘中读取最新的扩展配置，确保获取到最新的MCP服务器列表和工具状态
            extensions_config = ExtensionsConfig.from_file()

            if extensions_config.get_enabled_mcp_servers():
                mcp_tools = get_cached_mcp_tools()

                logger.info(f"Using {len(mcp_tools)} cached MCP tool(s)")
                # When tool_search is enabled, register MCP tools in the
                # deferred registry and add tool_search to builtin tools.
                # 如果工具搜索功能启用，将MCP工具注册到延迟注册表中，并将工具搜索工具添加到内置工具列表中。
                if config.tool_search.enabled:
                    from tools.builtins.tool_search import DeferredToolRegistry, set_deferred_registry
                    from tools.builtins.tool_search import tool_search as tool_search_tool

                    registry = DeferredToolRegistry()
                    for t in mcp_tools:
                        registry.register(t)
                    # 把注册表设置为全局可用
                    set_deferred_registry(registry)  
                    builtin_tools.append(tool_search_tool)
                    logger.info(f"Tool search active: {len(mcp_tools)} tools deferred")

        except ImportError:
            logger.warning("MCP module not available. Install 'langchain-mcp-adapters' package to enable MCP tools.")
        except Exception as e:
            logger.error(f"Failed to get cached MCP tools: {e}")

    # Add invoke_acp_agent tool if any ACP agents are configured
    # 加载ACP：ACP（Agent-to-Agent Protocol）, 谷歌定义的agent间通信协议 
    acp_tools: list[BaseTool] = []
    try:
        from config.acp_config import get_acp_agents
        from tools.builtins.invoke_acp_agent_tool import build_invoke_acp_agent_tool
        
        acp_agents = get_acp_agents()
        if acp_agents:
            acp_tools.append(build_invoke_acp_agent_tool(acp_agents))
            logger.info(f"Including invoke_acp_agent tool ({len(acp_agents)} agent(s): {list(acp_agents.keys())})")
    
    except Exception as e:
        logger.warning(f"Failed to load ACP tool: {e}")

    logger.info(f"Total tools loaded: {len(loaded_tools)}, built-in tools: {len(builtin_tools)}, MCP tools: {len(mcp_tools)}, ACP tools: {len(acp_tools)}")
    
    return loaded_tools + builtin_tools + mcp_tools + acp_tools