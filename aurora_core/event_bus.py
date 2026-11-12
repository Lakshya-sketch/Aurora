"""In-process async event bus for Aurora v2.

Design constraints (see architecture.md §1, §4):

  * asyncio-only. No threads, no locks for the publish path.
  * Topics are dotted strings (e.g. ``"llm.token"``). Subscribers
    can use exact match or the catch-all ``"*"``.
  * Per-topic delivery is fan-out, best-effort. If a subscriber
    raises, the bus either logs and continues (default) or
    propagates (opt-in), based on ``swallow_subscriber_errors``.
  * A bounded ring buffer of recent events is kept for tests and
    ``/health`` introspection. The bus is for *routing*, not
    storage; long-term history lives in plugins (e.g. memory_sqlite).

This is a Phase-0 implementation. When/if Aurora moves to a
distributed deployment, the *interface* stays — only the
implementation swaps to NATS/Redis. See architecture.md §9.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any, Awaitable, Callable, Deque

logger = logging.getLogger("aurora.event_bus")

# Type alias: an event handler is an async callable taking a topic
# string and a payload dict, returning None.
EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class EventBus:
    """Async pub/sub with topic strings and an in-memory history ring."""

    def __init__(
        self,
        history_size: int = 256,
        swallow_subscriber_errors: bool = True,
    ) -> None:
        if history_size < 0:
            raise ValueError("history_size must be >= 0")
        self._subs: dict[str, list[EventHandler]] = {}
        self._history: Deque[tuple[str, dict[str, Any]]] = deque(maxlen=history_size)
        self._swallow = swallow_subscriber_errors
        # Default asyncio lock used during subscribe/unsubscribe to
        # avoid mutating the handler list mid-publish. The publish
        # path itself does NOT hold this lock (handlers run after
        # the dispatch list is snapshotted).
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Register ``handler`` for ``topic``. Use ``"*"`` for catch-all.

        If the same handler is subscribed twice to the same topic, the
        second call is a no-op (idempotent).
        """
        if not isinstance(topic, str) or not topic:
            raise ValueError("topic must be a non-empty string")
        if not callable(handler):
            raise TypeError("handler must be callable")
        async with self._lock:
            bucket = self._subs.setdefault(topic, [])
            if handler not in bucket:
                bucket.append(handler)

    async def unsubscribe(self, topic: str, handler: EventHandler) -> bool:
        """Remove ``handler`` from ``topic``. Returns True if removed."""
        async with self._lock:
            bucket = self._subs.get(topic)
            if not bucket:
                return False
            try:
                bucket.remove(handler)
                return True
            except ValueError:
                return False

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, topic: str, payload: dict[str, Any] | None = None) -> int:
        """Fan-out ``payload`` to every subscriber of ``topic``.

        Returns the number of handlers that were scheduled. Each
        handler is awaited concurrently via ``asyncio.gather`` so
        that one slow subscriber does not block the others. The
        return-count includes the catch-all (``"*"``) bucket.
        """
        if not isinstance(topic, str) or not topic:
            raise ValueError("topic must be a non-empty string")
        payload = payload or {}
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict")

        # Snapshot the dispatch list under the lock so we don't hold
        # the lock while handlers run.
        async with self._lock:
            handlers = list(self._subs.get(topic, ()))
            handlers += list(self._subs.get("*", ()))

        # Record in history. We do this *before* dispatch so that
        # subscribers can themselves inspect history (e.g. the memory
        # plugin might want to see what just happened).
        self._history.append((topic, payload))

        if not handlers:
            logger.debug("no subscribers for topic=%r", topic)
            return 0

        if self._swallow:
            await asyncio.gather(
                *(self._safe_call(h, topic, payload) for h in handlers),
                return_exceptions=False,
            )
        else:
            # Propagate first failure; cancel the rest to avoid wasted work.
            # FIX: bare coroutines don't have `.done()`; we must wrap them in
            # Tasks first so we can cancel stragglers after one raises. The
            # original code used `c.done()` on coros and crashed during the
            # cleanup path on the first failing subscriber.
            tasks = [asyncio.create_task(h(topic, payload)) for h in handlers]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                raise
        return len(handlers)

    async def _safe_call(
        self, handler: EventHandler, topic: str, payload: dict[str, Any]
    ) -> None:
        try:
            await handler(topic, payload)
        except Exception:
            # Swallow + log so one bad plugin doesn't kill the bus.
            logger.exception(
                "subscriber %r raised on topic=%r; continuing",
                getattr(handler, "__qualname__", handler),
                topic,
            )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def history(self) -> list[tuple[str, dict[str, Any]]]:
        """Return a copy of recent events (oldest first)."""
        return list(self._history)

    def subscriber_count(self, topic: str | None = None) -> int:
        if topic is None:
            return sum(len(v) for v in self._subs.values())
        return len(self._subs.get(topic, ()))

    def topics(self) -> list[str]:
        return sorted(self._subs.keys())
