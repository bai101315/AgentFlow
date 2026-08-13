from __future__ import annotations

from skill.action_parsing import parse_actions_json


def test_parse_pure_object():
    assert parse_actions_json('{"actions": [{"action": "noop"}]}') == [{"action": "noop"}]


def test_parse_pure_array():
    assert parse_actions_json('[{"action": "noop"}]') == [{"action": "noop"}]


def test_parse_fenced_object():
    raw = '```json\n{"actions": [{"action": "noop"}]}\n```'
    assert parse_actions_json(raw) == [{"action": "noop"}]


def test_parse_fenced_array():
    raw = '```json\n[{"action": "noop"}]\n```'
    assert parse_actions_json(raw) == [{"action": "noop"}]


def test_parse_prose_wrapped_object():
    raw = 'Here you go:\n{"actions": [{"action": "noop"}]}\nDone.'
    assert parse_actions_json(raw) == [{"action": "noop"}]


def test_parse_prose_wrapped_array():
    raw = 'Sure: [{"action": "noop"}] -- that is all'
    assert parse_actions_json(raw) == [{"action": "noop"}]


def test_parse_filters_non_dict_items():
    raw = '{"actions": [{"action": "noop"}, "junk", 42]}'
    assert parse_actions_json(raw) == [{"action": "noop"}]


def test_parse_empty_actions_object_returns_empty():
    assert parse_actions_json('{"actions": []}') == []


def test_parse_garbage_returns_empty():
    assert parse_actions_json("totally not json at all") == []


def test_parse_empty_string_returns_empty():
    assert parse_actions_json("") == []
