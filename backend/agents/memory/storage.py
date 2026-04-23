
"""Memory storage providers."""

import abc
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.agents_config import AGENT_NAME_PATTERN
from config.memory_config import get_memory_config
from config.paths import get_paths

logger = logging.getLogger(__name__)

def utc_now_iso_z() -> str:
    """Current UTC time as ISO-8601 with ``Z`` suffix (matches prior naive-UTC output)."""
    return datetime.now(UTC).isoformat().removesuffix("+00:00") + "Z"

def create_empty_memory() -> dict[str, Any]:
    """Create an empty memory structure."""
    return {
        "version": "1.0",
        "lastUpdated": utc_now_iso_z(),
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }

class MemoryStorage(abc.ABC):
    """Abstract base class for memory storage providers."""
    
    @abc.abstractmethod
    def load(self, agent_name: str | None = None) -> dict[str, Any]:
        """Load memory data for the given agent."""
        pass

    @abc.abstractmethod
    def reload(self, agent_name: str | None = None) -> dict[str, Any]:
        """Force reload memory data for the given agent."""
        pass

    @abc.abstractmethod
    def save(self, memory_data: dict[str, Any], agent_name: str | None = None) -> bool:
        """Save memory data for the given agent."""
        pass

class FileMemoryStorage(MemoryStorage):
    """File-based memory storage provider."""
    
    def __init__(self):
        """Initialize the file memory storage."""
        # Per-agent memory cache: keyed by agent_name (None = global)
        # Value: (memory_data, file_mtime)
        self._memory_cache: dict[str | None, tuple[dict[str, Any], float | None]] = {}

    def _validate_agent_name(self, agent_name: str) -> None:
        """Validate that the agent name is safe to use in filesystem paths.

        Uses the repository's established AGENT_NAME_PATTERN to ensure consistency
        across the codebase and prevent path traversal or other problematic characters.
        使用AGENT_NAME_PATTERN确保代码库中名称的一致性
        并防止路径遍历或其他问题字符。
        AGENT_NAME_PATTERN: 只能包含：大写字母、小写字母、数字、连字符（-）;长度至少为1
        """
        if not agent_name:
            raise ValueError("Agent name must be a non-empty string.")
        if not AGENT_NAME_PATTERN.match(agent_name):
            raise ValueError(f"Invalid agent name {agent_name!r}: names must match {AGENT_NAME_PATTERN.pattern}")  

    def _get_memory_file_path(self, agent_name: str | None = None) -> Path:
        """Get the path to the memory file."""
        if agent_name is not None:
            self._validate_agent_name(agent_name)
            # 单例模式：懒加载，每个 agent_name 对应一个 memory 文件，首次访问时创建并缓存路径
            return get_paths().agent_memory_file(agent_name)

        # 全局 memory 文件路径
        config = get_memory_config()
        if config.storage_path:
            p = Path(config.storage_path)
            return p if p.is_absolute() else get_paths().base_dir / p
        
        return get_paths().memory_file

    def _load_memory_from_file(self, agent_name: str | None = None) -> dict[str, Any]:
        """Load memory data from file."""
        
        file_path = self._get_memory_file_path(agent_name)
        if not file_path.exists():
            return create_empty_memory()
        
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory file: %s", e)
            return create_empty_memory()
        

    def load(self, agent_name: str | None = None) -> dict[str, Any]:
        """Load memory data (cached with file modification time check)."""
        file_path = self._get_memory_file_path(agent_name)

        try:
            current_mtime = file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            current_mtime = None
        
        # _memory_cache的数据结构
        # Key: agent_name; Value: (memory_data, file_mtime)
        cached = self._memory_cache.get(agent_name)

        # 如果没有缓存，或者文件修改时间与缓存的不同，则重新加载
        if cached is None or cached[1] != current_mtime:
            memory_data = self._load_memory_from_file(agent_name)
            self._memory_cache[agent_name] = (memory_data, current_mtime)
            return memory_data
        return cached[0]
    
    def reload(self, agent_name: str | None = None) -> dict[str, Any]:
        """Reload memory data from file, forcing cache invalidation."""

        file_path = self._get_memory_file_path(agent_name)
        memory_data = self._load_memory_from_file(agent_name)

        try:
            mtime = file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            mtime = None
        
        self._memory_cache[agent_name] = (memory_data, mtime)
        return memory_data
    
    # 增
    def save(self, memory_data: dict[str, Any], agent_name: str | None = None) -> bool:
        """Save memory data to file and update cache."""
        file_path = self._get_memory_file_path(agent_name)

        # print(f"Saving memory for agent {agent_name!r} to {file_path}")

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            memory_data["lastUpdated"] = utc_now_iso_z()
            
            # 安全写入策略：先写临时文件；如果直接写源文件，可能会导致原文件损坏、数据丢失

            temp_path = file_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(memory_data, f, indent=2, ensure_ascii=False)
            # replace 是文件系统的原子操作；直接用临时文件覆盖原文件。
            # 即使在替换瞬间发生故障，也只会出现 “要么是新文件、要么是旧文件” 的情况，绝不会出现 “文件损坏一半” 的中间状态。
            temp_path.replace(file_path)
            
            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = None

            self._memory_cache[agent_name] = (memory_data, mtime)
            logger.info("Memory saved to %s", file_path)
            return True

        except OSError as e:
            logger.error("Failed to save memory file: %s", e)
            return False

# 单例模式：全局唯一的 MemoryStorage 实例，使用线程锁确保线程安全的懒加载初始化
_storage_instance: MemoryStorage | None = None
_storage_lock = threading.Lock()


def get_memory_storage() -> MemoryStorage:
    """Get the configured memory storage instance."""
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance
    
    with _storage_lock:
        # 确保只创建一个实例，防止 “两个线程同时通过第一重检查，其中一个先创建完实例，另一个还在等锁” 的情况
        if _storage_instance is not None:
            return _storage_instance
        config = get_memory_config()
        storage_class_path = config.storage_class

        try:
            # 从右边分割，只分割一次
            # storage_class_path: 
            # deerflow.agents.memory.storage.FileMemoryStorage
            module_path, class_name = storage_class_path.rsplit(".", 1)
            import importlib
            
            # 1: 根据模块路径字符串，动态导入模块对象。
            # 2: 从模块中获取类对象（通过类名字符串反射获取）。
            module = importlib.import_module(module_path)
            storage_class = getattr(module, class_name)

            # Validate that the configured storage is a MemoryStorage implementation
            # 验证：1，是一个类；2，是 MemoryStorage 的子类
            if not isinstance(storage_class, type):
                raise TypeError(f"Configured memory storage '{storage_class_path}' is not a class: {storage_class!r}")
            if not issubclass(storage_class, MemoryStorage):
                raise TypeError(f"Configured memory storage '{storage_class_path}' is not a subclass of MemoryStorage")

            _storage_instance = storage_class()

        
        except Exception as e:
            logger.error(
                "Failed to load memory storage %s, falling back to FileMemoryStorage: %s",
                storage_class_path,
                e,
            )
            _storage_instance = FileMemoryStorage()
        
        return _storage_instance
