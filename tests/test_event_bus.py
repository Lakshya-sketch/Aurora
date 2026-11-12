"""Tests for ``aurora_core.event_bus.EventBus``.

These tests run against the in-process bus implementation that the
Phase 0 microkernel uses. They are *not* integration tests — they
do not spin up a real kernel; the bus is exercised directly.
"""

from __future__ import annotations

import asyncio

import pytest

from aurora_core.event_bus import EventBus


async def test_subscribe_and_publish_calls_handler() -> None:
    bus = EventBus()
    received: list[tuple[str, dict]] = []

    async def handler(topic: str, payload: dict) -> None:
        received.append((topic, payload))

    await bus.subscribe("foo", handler)
    n = await bus.publish("foo", {"x": 1})

    assert n == 1
    assert received == [("foo", {"x": 1})]


async def test_publish_with_no_subscribers_returns_zero() -> None:
    bus = EventBus()
    assert await bus.publish("nothing.here") == 0


async def test_multiple_subscribers_fan_out() -> None:
    bus = EventBus()
    a, b = [], []

    async def ha(topic, payload): a.append(payload)
    async def hb(topic, payload): b.append(payload)

    await bus.subscribe("t", ha)
    await bus.subscribe("t", hb)
    n = await bus.publish("t", {"k": "v"})

    assert n == 2
    assert a == [{"k": "v"}]
    assert b == [{"k": "v"}]


async def test_catchall_subscriber_receives_every_topic() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def catchall(topic, payload): seen.append(topic)

    await bus.subscribe("*", catchall)
    await bus.publish("a", {})
    await bus.publish("b", {})
    await bus.publish("c", {})

    assert seen == ["a", "b", "c"]


async def test_handler_exception_is_swallowed_by_default() -> None:
    bus = EventBus()
    ok_called = []

    async def bad(topic, payload): raise RuntimeError("boom")
    async def good(topic, payload): ok_called.append(topic)

    await bus.subscribe("t", bad)
    await bus.subscribe("t", good)
    n = await bus.publish("t", {})

    assert n == 2  # both scheduled, bad one swallowed
    assert ok_called == ["t"]


async def test_handler_exception_propagates_when_not_swallowed() -> None:
    bus = EventBus(swallow_subscriber_errors=False)

    async def bad(topic, payload): raise RuntimeError("boom")
    await bus.subscribe("t", bad)

    with pytest.raises(RuntimeError, match="boom"):
        await bus.publish("t", {})


async def test_history_is_bounded_ring() -> None:
    bus = EventBus(history_size=3)
    for i in range(5):
        await bus.publish("t", {"i": i})
    hist = bus.history()
    assert len(hist) == 3
    # oldest first; only the last 3 should remain.
    assert [p["i"] for _, p in hist] == [2, 3, 4]


async def test_unsubscribe_removes_handler() -> None:
    bus = EventBus()
    seen = []

    async def h(topic, payload): seen.append(topic)
    await bus.subscribe("t", h)
    await bus.publish("t", {})
    removed = await bus.unsubscribe("t", h)
    await bus.publish("t", {})

    assert removed is True
    assert seen == ["t"]


async def test_duplicate_subscribe_is_idempotent() -> None:
    bus = EventBus()
    seen = []

    async def h(topic, payload): seen.append(topic)
    await bus.subscribe("t", h)
    await bus.subscribe("t", h)
    await bus.publish("t", {})

    assert bus.subscriber_count("t") == 1
    assert seen == ["t"]


async def test_invalid_topic_or_payload_raises() -> None:
    bus = EventBus()
    with pytest.raises(ValueError):
        await bus.publish("", {})
    with pytest.raises(ValueError):
        await bus.subscribe("", lambda *a, **k: None)  # noqa: E731
    with pytest.raises(TypeError):
        await bus.publish("ok", "not a dict")  # type: ignore[arg-type]
