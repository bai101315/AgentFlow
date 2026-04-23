"""Memory module for DeerFlow.

This module provides a global memory mechanism that:
- Stores user context and conversation history in memory.json
- Uses LLM to summarize and extract facts from conversations
- Injects relevant memory into system prompts for personalized responses
该模块提供了一个全局内存机制，功能包括：
- 将用户上下文和对话历史记录存储在 memory.json 文件中
- 使用 LLM 从对话中总结和提取事实
- 将相关内存信息注入系统提示，以实现个性化回复
"""

from agents.memory.prompt import (
    # FACT_EXTRACTION_PROMPT,
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
    format_memory_for_injection,
)
from agents.memory.queue import (
    ConversationContext,
    MemoryUpdateQueue,
    get_memory_queue,
    # reset_memory_queue,
)
from agents.memory.storage import (
    FileMemoryStorage,
    MemoryStorage,
    get_memory_storage,
)
from agents.memory.updater import (
    MemoryUpdater,
    clear_memory_data,
    # delete_memory_fact,
    get_memory_data,
    reload_memory_data,
    # update_memory_from_conversation,
)

__all__ = [
    # Prompt utilities
    "MEMORY_UPDATE_PROMPT",
    # "FACT_EXTRACTION_PROMPT",
    "format_memory_for_injection",
    "format_conversation_for_update",
    # Queue
    "ConversationContext",
    "MemoryUpdateQueue",
    "get_memory_queue",
    # "reset_memory_queue",
    # Storage
    "MemoryStorage",
    "FileMemoryStorage",
    "get_memory_storage",
    # Updater
    "MemoryUpdater",
    "clear_memory_data",
    # "delete_memory_fact",
    "get_memory_data",
    "reload_memory_data",
    # "update_memory_from_conversation",
]
