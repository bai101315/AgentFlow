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
    start_turn: int = 0
    end_turn: int = 0
    trigger: str = "turns"


@dataclass
class _SessionState:
    seen_user_turns: int = 0
    pending_messages: list[Any] = field(default_factory=list)
    pending_start_turn: int = 0
    correction_detected: bool = False
    reinforcement_detected: bool = False
    agent_name: str | None = None
    time_timer: threading.Timer | None = None
    pending_started_at: float | None = None

class MemoryUpdateQueue:
    """Queue for memory updates with debounce mechanism.

    This queue collects conversation contexts and processes them after
    a configurable debounce period. Multiple conversations received within
    the debounce window are batched together.
    """
    # 用于内存更新的队列，具有防抖机制。
    # 此队列收集会话上下文，并在一段可配置的防抖时间后处理。
    # 防抖窗口内收到的多个会话将被批量处理。
    def __init__(self):
        """Initialize the memory update queue."""
        self._queue: list[ConversationContext] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._processing = False
        self._sessions: dict[str, _SessionState] = {}

    @staticmethod
    def _user_indexes(messages: list[Any]) -> list[int]:
        return [i for i, message in enumerate(messages) if getattr(message, "type", None) == "human"]

    def record_turn(
        self,
        thread_id: str,
        messages: list[Any],
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> bool:
        """Record only the newest user turn from a session snapshot.

        Returns ``True`` when a complete ``update_every_turns`` batch was queued.
        The session cursor is independent for each thread and never stores the
        complete conversation history.
        """
        config = get_memory_config()
        if not config.enabled:
            return False
        user_indexes = self._user_indexes(messages)
        with self._lock:
            session = self._sessions.setdefault(thread_id, _SessionState())
            if len(user_indexes) <= session.seen_user_turns:
                return False
            new_user_index = user_indexes[session.seen_user_turns]
            new_messages = list(messages[new_user_index:])
            session.seen_user_turns += 1
            if not session.pending_messages:
                session.pending_start_turn = session.seen_user_turns
                session.pending_started_at = time.monotonic()
            session.pending_messages.extend(new_messages)
            session.agent_name = agent_name or session.agent_name
            session.correction_detected |= correction_detected
            session.reinforcement_detected |= reinforcement_detected and not correction_detected
            interval = config.update_every_turns
            if session.seen_user_turns % interval != 0:
                if session.pending_start_turn == session.seen_user_turns:
                    self._start_time_timer(thread_id)
                return False
            batch = ConversationContext(
                thread_id=thread_id,
                messages=session.pending_messages[-self._bounded_batch_messages(session.pending_messages, interval):],
                agent_name=agent_name,
                correction_detected=session.correction_detected,
                reinforcement_detected=session.reinforcement_detected,
                start_turn=session.pending_start_turn,
                end_turn=session.seen_user_turns,
                trigger="turns",
            )
            if session.time_timer is not None:
                session.time_timer.cancel()
                session.time_timer = None
            session.pending_messages.clear()
            session.pending_start_turn = 0
            session.pending_started_at = None
            session.correction_detected = False
            session.reinforcement_detected = False
            self._enqueue_context(batch)
            self._reset_timer()
            return True

    def _start_time_timer(self, thread_id: str) -> None:
        config = get_memory_config()
        session = self._sessions[thread_id]
        if session.time_timer is not None:
            return
        timer = threading.Timer(getattr(config, "time_trigger_seconds", 300), self._time_trigger, args=(thread_id,))
        timer.daemon = True
        session.time_timer = timer
        timer.start()

    def _time_trigger(self, thread_id: str) -> None:
        with self._lock:
            session = self._sessions.get(thread_id)
            if session is None or not session.pending_messages:
                return
            session.time_timer = None
            context = ConversationContext(
                thread_id=thread_id,
                messages=list(session.pending_messages),
                agent_name=session.agent_name,
                correction_detected=session.correction_detected,
                reinforcement_detected=session.reinforcement_detected,
                start_turn=session.pending_start_turn,
                end_turn=session.seen_user_turns,
                trigger="time",
            )
            session.pending_messages.clear()
            session.pending_start_turn = 0
            session.pending_started_at = None
            session.correction_detected = False
            session.reinforcement_detected = False
            self._enqueue_context(context)
            self._reset_timer()

    @staticmethod
    def _bounded_batch_messages(messages: list[Any], max_turns: int) -> int:
        """Return a suffix ending at at most ``max_turns`` human turns."""
        indexes = [i for i, msg in enumerate(messages) if getattr(msg, "type", None) == "human"]
        return len(messages) if len(indexes) <= max_turns else len(messages) - indexes[-max_turns]

    def should_update(self, thread_id: str) -> bool:
        with self._lock:
            if any(context.thread_id == thread_id for context in self._queue):
                return True
            session = self._sessions.get(thread_id)
            if not session or not session.pending_messages:
                return False
            config = get_memory_config()
            return (
                session.seen_user_turns % config.update_every_turns == 0
                or (
                    session.pending_started_at is not None
                    and time.monotonic() - session.pending_started_at >= getattr(config, "time_trigger_seconds", 300)
                )
            )

    def take_pending_batch(self, thread_id: str) -> ConversationContext | None:
        with self._lock:
            for context in self._queue:
                if context.thread_id == thread_id:
                    self._queue.remove(context)
                    return context
        return None

    def _replace_context(self, context: ConversationContext) -> None:
        self._queue = [item for item in self._queue if item.thread_id != context.thread_id]
        self._queue.append(context)

    def _enqueue_context(self, context: ConversationContext) -> None:
        """Append a generated batch without dropping another batch for the thread."""
        self._queue.append(context)

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
            existing_context = next((context for context in self._queue if context.thread_id == thread_id), None)

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
            self._replace_context(context)
            
            # Reset or start the debounce timer
            # 重置或启动防抖定时器
            self._reset_timer()
        logger.info("Memory update queued for thread %s, queue size: %d", thread_id, len(self._queue))

    def _reset_timer(self) -> None:
        """Reset the debounce timer."""
        config = get_memory_config()
        
        # 说明已经存在一个计时器，需要重置
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

        try:
            self._process_contexts(contexts_to_process)
        finally:
            with self._lock:
                self._processing = False

    def _process_contexts(self, contexts_to_process: list[ConversationContext]) -> None:
        """Run memory updates for a batch of contexts."""
        # Import here to avoid circular dependency
        from agents.memory.updater import MemoryUpdater

        logger.info("Processing %d queued memory updates", len(contexts_to_process))

        updater = MemoryUpdater()

        for context in contexts_to_process:
            try:
                logger.info("Updating memory for thread %s", context.thread_id)
                try:
                    from skill.events import emit_event

                    emit_event(
                        "memory_started",
                        status="running",
                        thread_id=context.thread_id,
                        agent_name=context.agent_name,
                        origin="conversation",
                        execution_context="memory_update",
                        trigger=context.trigger,
                    )
                except Exception:
                    pass

                success = updater.update_memory(
                    messages=context.messages,
                    thread_id=context.thread_id,
                    agent_name=context.agent_name,
                    correction_detected=context.correction_detected,
                    reinforcement_detected=context.reinforcement_detected,
                    trigger=context.trigger,
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

    def flush(self, timeout: float = 60.0) -> int:
        """Synchronously process all pending contexts (for graceful shutdown).

        The debounce timer is a daemon thread: if the process exits before the
        timer fires, queued memory updates are lost and memory.json is never
        written.  Call this before shutdown so pending updates are flushed to
        disk.  If a batch is already being processed, waits for it to finish
        and then drains anything queued in the meantime.

        Returns:
            The number of contexts processed.
        """
        deadline = time.time() + timeout
        while True:
            with self._lock:
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
                if self._processing:
                    in_flight = True
                    pending: list[ConversationContext] = []
                else:
                    # Session tails below the interval are flushed on exit.
                    for thread_id, session in self._sessions.items():
                        if session.pending_messages:
                            self._enqueue_context(ConversationContext(
                                thread_id=thread_id,
                                messages=list(session.pending_messages),
                                start_turn=session.pending_start_turn,
                                end_turn=session.seen_user_turns,
                                correction_detected=session.correction_detected,
                                reinforcement_detected=session.reinforcement_detected,
                                agent_name=session.agent_name,
                                trigger="flush",
                            ))
                            session.pending_messages.clear()
                            session.pending_start_turn = 0
                            session.pending_started_at = None
                            session.correction_detected = False
                            session.reinforcement_detected = False
                        if session.time_timer is not None:
                            session.time_timer.cancel()
                            session.time_timer = None
                    in_flight = False
                    pending = self._queue.copy()
                    self._queue.clear()

            if in_flight:
                if time.time() >= deadline:
                    logger.warning("Memory flush timed out waiting for in-flight batch")
                    return 0
                time.sleep(0.05)
                continue

            if not pending:
                return 0

            self._process_contexts(pending)
            return len(pending)

    def discard_pending(self) -> int:
        """Cancel debounce work that has not started processing."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            discarded = len(self._queue)
            self._queue.clear()
            self._sessions.clear()
        if discarded:
            try:
                from skill.events import emit_event

                emit_event("memory_dropped", status="dropped", count=discarded)
            except Exception:
                pass
        return discarded


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
