# sandbox 中间层，跟其他中间层一样，可以集成到agent里面

import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from agents.thread_state import SandboxState, ThreadDataState
from sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)

class SandboxMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]

class SandboxMiddleware(AgentMiddleware[SandboxMiddlewareState]):
    """Create a sandbox environment and assign it to an agent.

    Lifecycle Management:
    - With lazy_init=True (default): Sandbox is acquired on first tool call
    - With lazy_init=False: Sandbox is acquired on first agent invocation (before_agent)
    - Sandbox is reused across multiple turns within the same thread
    - Sandbox is NOT released after each agent call to avoid wasteful recreation
    - Cleanup happens at application shutdown via SandboxProvider.shutdown()
    """

    # 创建sandbox环境，并分配给agent
    #  lazy_init=True（默认值）：沙箱在首次调用工具时获取;
    # lazy_init=False：沙箱在首次调用代理时获取（before_agent）
    # 同一线程的多个回合中可重复使用; 不会在每次代理调用后释放，以避免不必要的重新创建
    # 清理工作在应用程序关闭时通过 SandboxProvider.shutdown() 进行

    state_schema = SandboxMiddlewareState

    def __init__(self, lazy_init: bool = True):
        """Initialize sandbox middleware.

        Args:
            lazy_init: If True, defer sandbox acquisition until first tool call.
                      If False, acquire sandbox eagerly in before_agent().
                      Default is True for optimal performance.
        """
        super().__init__()
        self._lazy_init = lazy_init
    
    def _acquire_sandbox(self, thread_id: str) -> str:
        provider = get_sandbox_provider()
        sandbox_id = provider.acquire(thread_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id  
    
    # 这个函数的作用是：创建沙箱
    @override
    def before_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        # Skip acquisition if lazy_init is enabled
        if self._lazy_init:
            # 该方法直接调用父类的 before_agent 并返回其结果（通常父类实现为空操作，即返回 None）。
            return super().before_agent(state, runtime)   

        # Eager initialization (original behavior)
        # 如果当前 state 中还没有 sandbox 字段或值为 None，则需要为其分配一个沙箱。
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = (runtime.context or {}).get("thread_id")
            # thread_id 是沙箱隔离的最小单位；
            # 如果获取不到 thread_id，则无法绑定沙箱，此时直接返回父类方法结果（通常即放弃本次分配）
            if thread_id is None:
                return super().before_agent(state, runtime)
            sandbox_id = self._acquire_sandbox(thread_id)
            logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
            return {"sandbox": {"sandbox_id": sandbox_id}}

        # 直接调用父类方法，不修改 state。这意味着该线程之前已经分配过沙箱，本次调用无需重复获取
        return super().before_agent(state, runtime)
    
    @override
    def after_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        sandbox = state.get("sandbox")
        if sandbox is not None:
            sandbox_id = sandbox["sandbox_id"]
            logger.info(f"Releasing sandbox {sandbox_id}")
            get_sandbox_provider().release(sandbox_id)
            return None
        
        if (runtime.context or {}).get("sandbox_id") is not None:
            sandbox_id = runtime.context.get("sandbox_id")
            logger.info(f"Releasing sandbox {sandbox_id} from context")
            get_sandbox_provider().release(sandbox_id)
            return None
        
        # No sandbox to release
        return super().after_agent(state, runtime)