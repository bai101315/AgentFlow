from __future__ import annotations


def test_event_subscriber_receives_persisted_record(monkeypatch, real_events, tmp_path):
    import skill.events as events
    from observability.store import ObservabilityStore

    monkeypatch.setattr(events, "get_events_file", lambda: tmp_path / "events.jsonl")
    monkeypatch.setattr(ObservabilityStore, "insert_background_event", lambda self, event: None)

    received = []
    unsubscribe = events.subscribe(received.append)
    try:
        events.emit_event(
            "skill_create",
            skill="demo",
            action="create",
            file_path="SKILL.md",
        )
    finally:
        unsubscribe()

    assert received[0]["event"] == "skill_create"
    assert received[0]["skill"] == "demo"


def test_memory_change_counts_classify_fact_and_section_changes():
    from agents.memory.updater import _memory_change_counts

    before = {
        "user": {"workContext": {"summary": "old"}},
        "history": {},
        "facts": [{"id": "fact_old", "content": "old"}],
    }
    after = {
        "user": {"workContext": {"summary": "new"}},
        "history": {},
        "facts": [{"id": "fact_new", "content": "new"}],
    }

    assert _memory_change_counts(before, after) == {
        "created": 1,
        "updated": 1,
        "deleted": 1,
        "count": 3,
    }


def test_memory_event_preserves_provenance_and_token_usage(monkeypatch, tmp_path, real_events):
    import skill.events as events

    monkeypatch.setattr(events, "get_events_file", lambda: tmp_path / "events.jsonl")
    monkeypatch.setattr("observability.store.ObservabilityStore.insert_background_event", lambda self, event: None)
    received = []
    unsubscribe = events.subscribe(received.append)
    try:
        events.emit_event(
            "memory_completed",
            status="completed",
            origin="conversation",
            execution_context="memory_update",
            elapsed_ms=12.5,
            model_name="test-model",
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
        )
    finally:
        unsubscribe()

    assert received[0]["origin"] == "conversation"
    assert received[0]["execution_context"] == "memory_update"
    assert received[0]["total_tokens"] == 14


def test_memory_queue_triggers_only_on_tenth_incremental_turn(monkeypatch):
    from types import SimpleNamespace
    from agents.memory.queue import MemoryUpdateQueue
    import config.memory_config as memory_config

    monkeypatch.setattr(memory_config, "_memory_config", SimpleNamespace(enabled=True, update_every_turns=10, time_trigger_seconds=300, debounce_seconds=60))
    queue = MemoryUpdateQueue()
    for turn in range(1, 11):
        messages = []
        for index in range(1, turn + 1):
            messages.extend([
                SimpleNamespace(type="human", content=f"user-{index}"),
                SimpleNamespace(type="ai", content=f"answer-{index}", tool_calls=[]),
            ])
        assert queue.record_turn("thread-a", messages) is (turn == 10)
        if queue._timer:
            queue._timer.cancel()
            queue._timer = None

    context = queue.take_pending_batch("thread-a")
    assert context is not None
    assert sum(getattr(message, "type", None) == "human" for message in context.messages) == 10
    assert context.trigger == "turns"


def test_memory_queue_time_trigger_flushes_pending_increment(monkeypatch):
    from types import SimpleNamespace
    from agents.memory.queue import MemoryUpdateQueue
    import config.memory_config as memory_config

    monkeypatch.setattr(memory_config, "_memory_config", SimpleNamespace(enabled=True, update_every_turns=10, time_trigger_seconds=300, debounce_seconds=60))
    queue = MemoryUpdateQueue()
    messages = [SimpleNamespace(type="human", content="user"), SimpleNamespace(type="ai", content="answer", tool_calls=[])]
    assert queue.record_turn("thread-time", messages) is False
    if queue._timer:
        queue._timer.cancel()
        queue._timer = None
    queue._time_trigger("thread-time")
    context = queue.take_pending_batch("thread-time")
    assert context is not None
    assert context.trigger == "time"
    assert sum(getattr(message, "type", None) == "human" for message in context.messages) == 1


def test_memory_compression_deduplicates_and_prunes_procedures():
    from agents.memory.storage import compress_memory_data

    data = {
        "version": "1.0",
        "user": {"topOfMind": {"summary": "one. two. three. four."}},
        "history": {},
        "facts": [
            {"id": "high", "content": "User prefers concise answers", "confidence": 0.95},
            {"id": "duplicate", "content": " user prefers concise answers ", "confidence": 0.8},
            {"id": "procedure", "content": "Run pytest then inspect traceback", "confidence": 1.0},
        ],
    }
    compressed = compress_memory_data(data, max_facts=40)
    assert len(compressed["facts"]) == 1
    assert compressed["facts"][0]["id"] == "high"
    assert compressed["user"]["topOfMind"]["summary"] == "one. two. three."


def test_memory_update_with_new_fact_does_not_raise_agent_name_error(monkeypatch):
    from types import SimpleNamespace
    import agents.memory.updater as updater_module
    import config.memory_config as memory_config

    monkeypatch.setattr(memory_config, "_memory_config", SimpleNamespace(
        enabled=True,
        model_name="test-model",
        fact_confidence_threshold=0.7,
        max_facts=40,
        storage_path="",
    ))
    memory = {"version": "1.1", "user": {}, "history": {}, "facts": []}

    class Response:
        content = '{"user": {}, "history": {}, "newFacts": [{"content": "User prefers concise answers", "category": "preference", "confidence": 1.0}], "factsToRemove": []}'
        response_metadata = {"model_name": "test-model", "token_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        usage_metadata = {}

    class Model:
        def invoke(self, _prompt):
            return Response()

    monkeypatch.setattr(updater_module, "get_memory_data", lambda _agent=None: memory.copy())
    monkeypatch.setattr(updater_module, "get_memory_storage", lambda: SimpleNamespace(save=lambda _data, _agent=None: True))
    monkeypatch.setattr(updater_module.MemoryUpdater, "_get_model", lambda _self: Model())
    assert updater_module.MemoryUpdater().update_memory(
        [SimpleNamespace(type="human", content="I prefer concise answers")],
        thread_id="thread-fact",
        agent_name="test",
        trigger="turns",
    ) is True
