"""Middleware that extends TodoListMiddleware with context-loss detection.

When the message history is truncated (e.g., by SummarizationMiddleware), the
original `write_todos` tool call and its ToolMessage can be scrolled out of the
active context window. This middleware detects that situation and injects a
reminder message so the model still knows about the outstanding todo list.

解决问题：
LangChain 中，TodoListMiddleware（基础待办中间件）的作用是让 AI 生成 / 管理「待办列表（Todo List）」
AI 会通过调用 write_todos 工具来创建 / 更新待办。

但是对话历史会被「截断 / 摘要」，比如 SummarizationMiddleware 会把早期的消息压缩 / 删除，节省 Token）
导致AI 之前调用 write_todos 的记录被删掉了 ——AI 「忘了」自己还有待办列表，后续就不会再更新待办，计划流程就断了
主要是给模型赛一个提醒：虽然之前的 write_todos 消息不见了，但待办列表还在，继续跟踪更新它。
"""

from __future__ import annotations

from typing import Any, override

# PlanningState：「计划状态」，包含待办列表、消息历史等核心数据（可以理解为 “状态容器”）；
# # Todo：「待办事项」，包含内容、状态等信息（可以理解为 “待办项”）；
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.todo import PlanningState, Todo
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime


def _todos_in_messages(messages: list[Any]) -> bool:
    """Return True if any AIMessage in *messages* contains a write_todos tool call.
    检查消息里有没有「创建待办」的记录
    """
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("name") == "write_todos":
                    return True
    return False

# 检查有没有已经注入过「待办提醒」
def _reminder_in_messages(messages: list[Any]) -> bool:
    """Return True if a todo_reminder HumanMessage is already present in *messages*."""
    for msg in messages:
        if isinstance(msg, HumanMessage) and getattr(msg, "name", None) == "todo_reminder":
            return True
    return False

# 把待办列表格式化成「模型能看懂的字符串」，比如：
# - [pending] 研究vibe coding的定义
# - [pending] 查找vibe coding的应用案例
def _format_todos(todos: list[Todo]) -> str:
    """Format a list of Todo items into a human-readable string."""
    lines: list[str] = []
    for todo in todos:
        status = todo.get("status", "pending")
        content = todo.get("content", "")
        lines.append(f"- [{status}] {content}")
    return "\n".join(lines)


class TodoMiddleware(TodoListMiddleware):
    """Extends TodoListMiddleware with `write_todos` context-loss detection.

    When the original `write_todos` tool call has been truncated from the message
    history (e.g., after summarization), the model loses awareness of the current
    todo list. This middleware detects that gap in `before_model` / `abefore_model`
    and injects a reminder message so the model can continue tracking progress.
    """

    @override
    def before_model(
        self,
        state: PlanningState,   # 核心状态：包含待办列表、消息历史等
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Inject a todo-list reminder when write_todos has left the context window."""
        todos: list[Todo] = state.get("todos") or []  # type: ignore[assignment]
        if not todos:
            return None

        messages = state.get("messages") or []
        if _todos_in_messages(messages):
            # write_todos is still visible in context — nothing to do.
            return None

        if _reminder_in_messages(messages):
            # A reminder was already injected and hasn't been truncated yet.
            return None

        # The todo list exists in state but the original write_todos call is gone.
        # Inject a reminder as a HumanMessage so the model stays aware.
        formatted = _format_todos(todos)
        reminder = HumanMessage(
            name="todo_reminder",
            content=(
                "<system_reminder>\n"
                "Your todo list from earlier is no longer visible in the current context window, "
                "but it is still active. Here is the current state:\n\n"
                f"{formatted}\n\n"
                "Continue tracking and updating this todo list as you work. "
                "Call `write_todos` whenever the status of any item changes.\n"
                "</system_reminder>"
            ),
        )
        return {"messages": [reminder]}

    @override
    async def abefore_model(
        self,
        state: PlanningState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        """Async version of before_model."""
        return self.before_model(state, runtime)
