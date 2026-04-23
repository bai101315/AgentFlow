"""Memory updater for reading, writing, and updating memory data."""

import json
import logging
import math
import re
import uuid
from typing import Any

from agents.memory.prompt import (
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
)
from agents.memory.storage import (
    create_empty_memory,
    get_memory_storage,
    utc_now_iso_z,
)
from config.memory_config import get_memory_config
from models.factory import create_chat_model

logger = logging.getLogger(__name__)

def _create_empty_memory() -> dict[str, Any]:
    """Backward-compatible wrapper around the storage-layer empty-memory factory."""
    return create_empty_memory()

def _save_memory_to_file(memory_data: dict[str, Any], agent_name: str | None = None) -> bool:
    """Backward-compatible wrapper around the configured memory storage save path."""
    return get_memory_storage().save(memory_data, agent_name)

def get_memory_data(agent_name: str | None = None) -> dict[str, Any]:
    """Get the current memory data via storage provider."""
    return get_memory_storage().load(agent_name)

def reload_memory_data(agent_name: str | None = None) -> dict[str, Any]:
    """Reload memory data via storage provider."""
    return get_memory_storage().reload(agent_name)

def import_memory_data(memory_data: dict[str, Any], agent_name: str | None = None) -> dict[str, Any]:
    """Persist imported memory data via storage provider.

    Args:
        memory_data: Full memory payload to persist.
        agent_name: If provided, imports into per-agent memory.

    Returns:
        The saved memory data after storage normalization.

    Raises:
        OSError: If persisting the imported memory fails.
    """
    storage = get_memory_storage()
    if not storage.save(memory_data, agent_name):
        raise OSError("Failed to save imported memory data")
    return storage.load(agent_name)

def clear_memory_data(agent_name: str | None = None) -> dict[str, Any]:
    """Clear all stored memory data and persist an empty structure."""
    cleared_memory = create_empty_memory()
    if not _save_memory_to_file(cleared_memory, agent_name):
        raise OSError("Failed to save cleared memory data")
    return cleared_memory


def _extract_text(content: Any) -> str:
    """Extract plain text from LLM response content (str or list of content blocks).

    Modern LLMs may return structured content as a list of blocks instead of a
    plain string, e.g. [{"type": "text", "text": "..."}]. Using str() on such
    content produces Python repr instead of the actual text, breaking JSON
    parsing downstream.

    String chunks are concatenated without separators to avoid corrupting
    chunked JSON/text payloads. Dict-based text blocks are treated as full text
    blocks and joined with newlines for readability.
    """

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        pending_str_parts: list[str] = []

        def flush_pending_str_parts() -> None:
            if pending_str_parts:
                pieces.append("".join(pending_str_parts))
                pending_str_parts.clear()

        for block in content:
            if isinstance(block, str):
                pending_str_parts.append(block)
            elif isinstance(block, dict):
                flush_pending_str_parts()
                text_val = block.get("text")
                if isinstance(text_val, str):
                    pieces.append(text_val)

        flush_pending_str_parts()
        return "\n".join(pieces)
    return str(content)



def _fact_content_key(content: Any) -> str | None:
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None
    return stripped.casefold()

# Matches sentences that describe a file-upload *event* rather than general
# file-related work.  Deliberately narrow to avoid removing legitimate facts
# such as "User works with CSV files" or "prefers PDF export".
_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"upload(?:ed|ing)?(?:\s+\w+){0,3}\s+(?:file|files?|document|documents?|attachment|attachments?)"
    r"|file\s+upload"
    r"|/mnt/user-data/uploads/"
    r"|<uploaded_files>"
    r")[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)

def _strip_upload_mentions_from_memory(memory_data: dict[str, Any]) -> dict[str, Any]:
    """Remove sentences about file uploads from all memory summaries and facts.

    Uploaded files are session-scoped; persisting upload events in long-term
    memory causes the agent to search for non-existent files in future sessions.
    """
    # Scrub summaries in user/history sections
    for section in ("user", "history"):
        section_data = memory_data.get(section, {})
        for _key, val in section_data.items():
            if isinstance(val, dict) and "summary" in val:
                cleaned = _UPLOAD_SENTENCE_RE.sub("", val["summary"]).strip()
                cleaned = re.sub(r"  +", " ", cleaned)
                val["summary"] = cleaned

    # Also remove any facts that describe upload events
    facts = memory_data.get("facts", [])
    if facts:
        memory_data["facts"] = [f for f in facts if not _UPLOAD_SENTENCE_RE.search(f.get("content", ""))]

    return memory_data

class MemoryUpdater:
    """Updates memory using LLM based on conversation context."""
    def __init__(self, model_name: str | None = None):
        """Initialize the memory updater.

        Args:
            model_name: Optional model name to use. If None, uses config or default.
        """
        self._model_name = model_name

    def _get_model(self):
        """Get the model for memory updates."""
        config = get_memory_config()
        model_name = self._model_name or config.model_name
        return create_chat_model(name=model_name, thinking_enabled=False)

    def update_memory(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        agent_name: str | None = None,
        correction_detected: bool = False,
        reinforcement_detected: bool = False,
    ) -> bool:
        
        """Update memory based on conversation messages.
        根据对话消息更新内存

        Args:
            messages: List of conversation messages.
            thread_id: Optional thread ID for tracking source.
            agent_name: If provided, updates per-agent memory. If None, updates global memory.
            correction_detected: Whether recent turns include an explicit correction signal.
            reinforcement_detected: Whether recent turns include a positive reinforcement signal.

        Returns:
            True if update was successful, False otherwise.
        """
        config = get_memory_config()
        if not config.enabled:
            return False

        if not messages:
            return False
        
        try:
            # Get current memory
            current_memory = get_memory_data(agent_name)

            # Format conversation for prompt
            conversation_text = format_conversation_for_update(messages)

            if not conversation_text.strip():
                return False
            
            # Build prompt
            correction_hint = ""
            if correction_detected:
                correction_hint = (
                    "IMPORTANT: Explicit correction signals were detected in this conversation. "
                    "Pay special attention to what the agent got wrong, what the user corrected, "
                    "and record the correct approach as a fact with category "
                    '"correction" and confidence >= 0.95 when appropriate.'
                )
            if reinforcement_detected:
                reinforcement_hint = (
                    "IMPORTANT: Positive reinforcement signals were detected in this conversation. "
                    "The user explicitly confirmed the agent's approach was correct or helpful. "
                    "Record the confirmed approach, style, or preference as a fact with category "
                    '"preference" or "behavior" and confidence >= 0.9 when appropriate.'
                )
                correction_hint = (correction_hint + "\n" + reinforcement_hint).strip() if correction_hint else reinforcement_hint
            
            # 当前记忆+对话内容+提示语
            prompt = MEMORY_UPDATE_PROMPT.format(
                current_memory=json.dumps(current_memory, indent=2),
                conversation=conversation_text,
                correction_hint=correction_hint,
            )
            # print(f"====current_memory： {current_memory}====")

            # Call LLM
            model = self._get_model()
            response = model.invoke(prompt)
            # 从AI的响应中提取纯文本，去除首位空格
            response_text = _extract_text(response.content).strip()

            # Parse response
            # Remove markdown code blocks if present
            # 清楚markdown代码块，如果响应被包裹在``` ```中，则提取其中内容
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            # print(f"====response_text {response_text}====")

            update_data = json.loads(response_text)

            # Apply updates
            # 根据当前记忆和LLM返回的更新数据，生成新的记忆数据
            updated_memory = self._apply_updates(current_memory, update_data, thread_id)
            
            # Strip file-upload mentions from all summaries before saving.
            # Uploaded files are session-scoped and won't exist in future sessions,
            # so recording upload events in long-term memory causes the agent to
            # try (and fail) to locate those files in subsequent conversations.
            # 删除所有包含上传文件的摘要
            updated_memory = _strip_upload_mentions_from_memory(updated_memory)

            return get_memory_storage().save(updated_memory, agent_name)

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response for memory update: %s", e)
            return False
        except Exception as e:
            logger.exception("Memory update failed: %s", e)
            return False
    
    def _apply_updates(
        self,
        current_memory: dict[str, Any],
        update_data: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply LLM-generated updates to memory.

        Args:
            current_memory: Current memory data.
            update_data: Updates from LLM.
            thread_id: Optional thread ID for tracking.

        Returns:
            Updated memory data.
        """
        config = get_memory_config()
        now = utc_now_iso_z()

        # Update user sections
        user_updates = update_data.get("user", {})
        for section in ["workContext", "personalContext", "topOfMind"]:
            section_data = user_updates.get(section, {})
            # 当明确的纠正信号被检测到时，才会将LLM返回的内容更新到记忆中，
            # 这样可以避免错误信息被记录到记忆里导致后续对话的误导。
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["user"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }
        
        # Update history sections
        history_updates = update_data.get("history", {})
        for section in ["recentMonths", "earlierContext", "longTermBackground"]:
            section_data = history_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["history"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }
    
        # Remove facts
        facts_to_remove = set(update_data.get("factsToRemove", []))
        if facts_to_remove:
            current_memory["facts"] = [f for f in current_memory.get("facts", []) if f.get("id") not in facts_to_remove]

        # Add new facts
        existing_fact_keys = {fact_key for fact_key in (_fact_content_key(fact.get("content")) for fact in current_memory.get("facts", [])) if fact_key is not None}
        new_facts = update_data.get("newFacts", [])
        for fact in new_facts:
            confidence = fact.get("confidence", 0.5)
            if confidence >= config.fact_confidence_threshold:
                raw_content = fact.get("content", "")
                if not isinstance(raw_content, str):
                    continue
                normalized_content = raw_content.strip()
                fact_key = _fact_content_key(normalized_content)
                if fact_key is not None and fact_key in existing_fact_keys:
                    continue

                fact_entry = {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": normalized_content,
                    "category": fact.get("category", "context"),
                    "confidence": confidence,
                    "createdAt": now,
                    "source": thread_id or "unknown",
                }
                source_error = fact.get("sourceError")
                if isinstance(source_error, str):
                    normalized_source_error = source_error.strip()
                    if normalized_source_error:
                        fact_entry["sourceError"] = normalized_source_error
                current_memory["facts"].append(fact_entry)
                if fact_key is not None:
                    existing_fact_keys.add(fact_key)
        
        
        # Enforce max facts limit
        if len(current_memory["facts"]) > config.max_facts:
            # Sort by confidence and keep top ones
            current_memory["facts"] = sorted(
                current_memory["facts"],
                key=lambda f: f.get("confidence", 0),
                reverse=True,
            )[: config.max_facts]

        return current_memory