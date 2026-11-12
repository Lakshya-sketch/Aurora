"""Plugin contract for Aurora v2.

This module defines the single contract every plugin must satisfy.
The contract is deliberately tiny:

    class MyPlugin(BasePlugin):
        name = "my_plugin"
        version = "0.1.0"
        capabilities = ["..."]      # tools/services this plugin offers
        requires     = ["..."]      # capabilities it needs from siblings

        async def setup(self, ctx): ...
        async def shutdown(self): ...
        async def handle_event(self, topic, payload): ...

The kernel discovers subclasses in ``plugins/*/plugin.py`` and treats
them as swappable units. Plugins MUST NOT import each other — they
talk only via the EventBus injected through ``PluginContext``.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # avoid runtime import cycle
    from aurora_core.event_bus import EventBus


@dataclass
class PluginContext:
    """Handle a plugin receives at setup time.

    The plugin stores this and uses it to publish events and read
    config. It deliberately does NOT expose direct access to other
    plugins — cross-plugin coordination happens through the bus.
    """

    name: str
    config: dict[str, Any]
    bus: "EventBus"
    logger: logging.Logger = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = logging.getLogger(f"aurora.plugin.{self.name}")


class BasePlugin(abc.ABC):
    """Abstract base for every Aurora plugin.

    Subclasses set the four class attributes and implement the three
    async methods. The kernel calls them in this order:

        1. ``__init__``           — pure-Python, no I/O
        2. ``setup(ctx)``         — open files, register subscribers
        3. ``handle_event(...)``   — invoked for every subscribed topic
        4. ``shutdown()``          — release resources
    """

    name: str = ""
    version: str = "0.0.0"
    capabilities: list[str] = []
    requires: list[str] = []

    # Topics the plugin wants to receive. Listeners are wired by the
    # kernel during ``setup``. Use ``"*"`` for the catch-all (tests
    # and debug plugins only).
    subscribes: list[str] = []

    def __init__(self) -> None:
        if not self.name:
            # Auto-derive from class name if author forgot to set it.
            self.name = self.__class__.__name__
        self._ctx: PluginContext | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def setup(self, ctx: PluginContext) -> None:
        """Open connections, subscribe to topics, warm caches."""

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """Release everything opened in setup. Must be idempotent."""

    async def handle_event(self, topic: str, payload: dict[str, Any]) -> None:
        """Default no-op. Override to handle subscribed topics.

        Exceptions raised here are caught by the bus (when configured
        to swallow) and logged — they do not crash the kernel.
        """

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Plain-dict summary used by /health and the registry."""
        return {
            "name": self.name,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "requires": list(self.requires),
            "subscribes": list(self.subscribes),
        }
