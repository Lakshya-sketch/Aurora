"""Tests for the IPC envelope contract.

These run against ``aurora_core.transport.ipc`` only — no Quart, no
kernel. They prove the wire format the renderer speaks is stable.
"""

from __future__ import annotations

import pytest

from aurora_core.transport.ipc import (
    envelope_error,
    envelope_event,
    envelope_pong,
    envelope_publish,
    parse_envelope,
)


def test_envelope_event_round_trip() -> None:
    env = envelope_event("llm.token", {"text": "hi"})
    assert env == {"type": "event", "topic": "llm.token", "payload": {"text": "hi"}}
    # The renderer should be able to dispatch purely on `type`.
    assert env["type"] == "event"


def test_envelope_event_validates_inputs() -> None:
    with pytest.raises(ValueError):
        envelope_event("", {"x": 1})
    with pytest.raises(TypeError):
        envelope_event("ok", "not a dict")  # type: ignore[arg-type]


def test_envelope_publish_helper_shape() -> None:
    env = envelope_publish("user.message", {"text": "hello"})
    assert env["type"] == "publish"
    assert env["topic"] == "user.message"
    assert env["payload"] == {"text": "hello"}


def test_envelope_pong_and_error() -> None:
    assert envelope_pong() == {"type": "pong"}
    assert envelope_error("bad") == {"type": "error", "message": "bad"}


def test_parse_envelope_accepts_dict_and_json_string() -> None:
    a = parse_envelope({"type": "ping"})
    b = parse_envelope('{"type":"ping"}')
    assert a == {"type": "ping"}
    assert b == {"type": "ping"}


def test_parse_envelope_validates_publish_shape() -> None:
    assert parse_envelope({"type": "publish", "topic": "t", "payload": {"k": 1}}) == {
        "type": "publish",
        "topic": "t",
        "payload": {"k": 1},
    }
    # payload is optional, defaults to {}
    assert parse_envelope({"type": "publish", "topic": "t"}) == {
        "type": "publish",
        "topic": "t",
    }


def test_parse_envelope_rejects_bad_input() -> None:
    with pytest.raises(TypeError):
        parse_envelope(42)  # not dict or string
    with pytest.raises(ValueError):
        parse_envelope({})  # missing type
    with pytest.raises(ValueError):
        parse_envelope({"type": ""})  # empty type
    with pytest.raises(ValueError):
        parse_envelope('not json')  # invalid json
    with pytest.raises(ValueError):
        parse_envelope({"type": "unknown"})  # unknown type
    with pytest.raises(ValueError):
        parse_envelope({"type": "publish"})  # missing topic
    with pytest.raises(ValueError):
        parse_envelope({"type": "publish", "topic": ""})  # empty topic
    with pytest.raises(TypeError):
        parse_envelope({"type": "publish", "topic": "t", "payload": "not a dict"})
