"""Memory update queue with debounce mechanism."""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from config.memory_config import get_memory_config

logger = logging.getLogger(__name__)

@dataclass
class ConversationContext:
    """Context for a conversation to be processed for memory update."""

    thread_id: str
    messages: list[Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    agent_name: str | None = None
    correction_detected: bool = False
    reinforcement_detected: bool = False

class MemoryUpdateQueue:
    """Queue for memory updates with debounce mechanism.

    This queue collects conversation contexts and processes them after
    a configurable debounce period. Multiple conversations received within
    the debounce window are batched together.
    用于内存更新的队列，具有防抖机制。
    此队列收集会话上下文，并在一段可配置的防抖时间后处理。
    防抖窗口内收到的多个会话将被批量处理。
    """
    def __init__(self):
        """Initialize the memory update queue."""
        self._queue: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False

    def add(
        self,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> None:
        """Add a conversation to the update queue.

        Args:
            thread_id: The thread ID.
            messages: The conversation messages.
            agent_name: If provided, memory is stored per-agent. If None, uses global memory.
            correction_detected: Whether recent turns include an explicit correction signal.
            reinforcement_detected: Whether recent turns include a positive reinforcement signal.
        """

        config = get_memory_config()
        if not config.enabled:
            return
        
        with self._lock:
            existing_context = next(
                (context for context in self._queue if context.thread_id == thread_id),
                None,
            )

            merged_correction_detected = correction_detected or (existing_context.correction_detected if existing_context is not None else False)
            merged_reinforcement_detected = reinforcement_detected or (existing_context.reinforcement_detected if existing_context is not None else False)
            context = ConversationContext(
                thread_id=thread_id,
                messages=messages,
                agent_name=agent_name,
                correction_detected=merged_correction_detected,
                reinforcement_detected=merged_reinforcement_detected,
            )
            # Check if this thread already has a pending update
            # If so, replace it with the newer one

            # 如果队列中有其他thread_id，说明应该被合并处理
            self._queue = [c for c in self._queue if c.thread_id != thread_id]
            self._queue.append(context)
            
            # Reset or start the debounce timer
            # 重置或启动防抖定时器
            self._reset_timer()
        logger.info("Memory update queued for thread %s, queue size: %d", thread_id, len(self._queue))

    def _reset_timer(self) -> None:
        """Reset the debounce timer."""
        config = get_memory_config()
        
        # 说明已经村子啊一个计时器，需要重置
        if self._timer is not None:
            self._timer.cancel()
        
        # Start new timer
        # 1, 等待的秒数；2，时间到后要执行的函数，_process_queue；
        self._timer = threading.Timer(
            config.debounce_seconds,
            self._process_queue,
        )
        # 设置为守护线程，这样在主程序退出时不会因为这个线程还在等着执行而阻塞退出
        self._timer.daemon = True

        self._timer.start()
        logger.debug("Memory update timer set for %ss", config.debounce_seconds)

    def _process_queue(self) -> None:
        """Process all queued conversation contexts."""
        # Import here to avoid circular dependency
        from agents.memory.updater import MemoryUpdater

        with self._lock:
            # 如果正在处理，直接重新设置定时器，等当前处理完再处理新的
            if self._processing:
                # Already processing, reschedule
                self._reset_timer()
                return
            
            if not self._queue:
                return
            
            self._processing = True
            # 开始执行这一批，把当前队列中的内容复制出来，清空队列，
            # 这样在处理的过程中如果有新的加入，就会进入新的批次，不会干扰当前批次的处理
            contexts_to_process = self._queue.copy()
            self._queue.clear()
            self._timer = None

        logger.info("Processing %d queued memory updates", len(contexts_to_process))

        try:
            updater = MemoryUpdater()

            for context in contexts_to_process:
                try:
                    logger.info("Updating memory for thread %s", context.thread_id)

                    success = updater.update_memory(
                        messages=context.messages,
                        thread_id=context.thread_id,
                        agent_name=context.agent_name,
                        correction_detected=context.correction_detected,
                        reinforcement_detected=context.reinforcement_detected,
                    )
                    if success:
                        logger.info("Memory updated successfully for thread %s", context.thread_id)
                    else:
                        logger.warning("Memory update skipped/failed for thread %s", context.thread_id)

                except Exception as e:
                    logger.error("Error updating memory for thread %s: %s", context.thread_id, e)
                
                # Small delay between updates to avoid rate limiting
                # 如果有很多任务，每处理完一次就休息0.5s，避免过快处理完所有任务导致的速率限制问题
                if len(contexts_to_process) > 1:
                    time.sleep(0.5)
        # finally 的作用：无论上面的代码是成功执行完、还是中途报错跳出，这一块代码一定会执行，保证状态能被正确重置。
        finally:
            with self._lock:
                self._processing = False


# Global singleton instance
_memory_queue: MemoryUpdateQueue | None = None
_queue_lock = threading.Lock()

def get_memory_queue() -> MemoryUpdateQueue:
    """Get the global memory update queue singleton.

    Returns:
        The memory update queue instance.
    """
    global _memory_queue
    with _queue_lock:
        if _memory_queue is None:
            _memory_queue = MemoryUpdateQueue()
        return _memory_queue
