import logging

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from config.paths import get_paths

logger = logging.getLogger(__name__)

@tool
def setup_agent(
    soul: str,
    description: str,
    runtime: ToolRuntime,
) -> Command:
    """Setup the custom DeerFlow agent.
    Args:
        soul: Full SOUL.md content defining the agent's personality and behavior.
        description: One-line description of what the agent does.
    """
    # LangGraph 运行时（Runtime）调度执行的工具函数，用于根据提供的 SOUL.md 内容和描述信息，创建或更新一个自定义的 DeerFlow 代理。
    # ToolRuntime是工具执行时传入的上下文对象，它是工具和 LangGraph 运行时之间的桥梁
    # runtime.context：全局状态上下文（这里存了 agent_name）。
    # runtime.tool_call_id：本次工具调用的唯一 ID（必须在返回的 ToolMessage 中带上，否则 LangGraph 不知道这个消息对应哪次调用）。
    # runtime.config：运行时配置。
    # runtime.write：向全局状态写入数据的方法（不过现在推荐用 Command 来更新状态）
    
    # Command 是 LangGraph 定义的标准状态更新协议, 它统一了 “工具如何影响全局状态” 和 “工具如何返回消息” 这两个行为。
    # Command 的 update 字段会原子性地更新 LangGraph 的全局状态字典.
    # "created_agent_name": agent_name：把新创建的 Agent 名字写入全局状态，后续的节点（比如切换 Agent 的节点）可以直接读取这个值。
    # "messages": [...]：把工具返回的消息添加到全局的 messages 列表中，这个列表就是整个对话的历史。

    agent_name: str | None = runtime.context.get("agent_name") if runtime.context else None
    try:
        paths = get_paths()
        agent_dir = paths.agent_dir(agent_name) if agent_name else paths.base_dir
        agent_dir.mkdir(parents=True, exist_ok=True)
        
        # agent_dir: C:\Users\BAI\Desktop\project\deer-flow-study\.deer_flow
        # print(f"agent_dir: {agent_dir}")
        # soul: # 刷题记录回忆助手 - SOUL.md, 很多内容
        # description: description:专业的刷题记录回忆与分析助手，支持多平台刷题数据管理、历史记录查询、知识点回忆辅助及数据分析统计
        print(f"soul:{soul}, description:{description}")

        if agent_name:
            # If agent_name is provided, we are creating a custom agent in the agents/ directory
            config_data: dict = {"name": agent_name}
            if description:
                config_data["description"] = description
            
            config_file = agent_dir / "config.yaml"

            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
            
        soul_file = agent_dir / "SOUL.md"
        soul_file.write_text(soul, encoding="utf-8")
        
        logger.info(f"[agent_creator] Created agent '{agent_name}' at {agent_dir}")

        return Command(
            update={
                "created_agent_name": agent_name,
                "messages": [ToolMessage(content=f"Agent '{agent_name}' created successfully!", tool_call_id=runtime.tool_call_id)],
            }
        )

    except Exception as e:
        import shutil
        
        if agent_name and agent_dir.exists():
            # Cleanup the custom agent directory only if it was created but an error occurred during setup
            # 创建过程失败或者已经存在，清理这些垃圾资源
            shutil.rmtree(agent_dir)
        
        logger.error(f"[agent_creator] Failed to create agent '{agent_name}': {e}", exc_info=True)
        
        return Command(update={"messages": [ToolMessage(content=f"Error: {e}", tool_call_id=runtime.tool_call_id)]})
