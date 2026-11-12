"""Kernel: the lifecycle owner of Aurora v2.

Phase 0 responsibilities:

  1. Hold a single EventBus instance.
  2. Discover and instantiate plugins.
  3. Hand each plugin a ``PluginContext`` and call ``setup()``.
  4. Subscribe each plugin to its declared topics.
  5. Provide a clean shutdown path that calls every plugin's
     ``shutdown()`` (in reverse-load order).

Transport (HTTP/WS) and the orchestrator are added in later phases;
the kernel itself is intentionally small. See
``implementation_plan.md`` Phase 0 for the deliverable list.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aurora_core.config import get
from aurora_core.event_bus import EventBus
from aurora_core.plugin_base import BasePlugin, PluginContext
from aurora_core.registry import discover_plugins

logger = logging.getLogger("aurora.kernel")


class KernelError(RuntimeError):
    """Raised when the kernel cannot start up properly."""


class Kernel:
    """Top-level microkernel object.

    Lifecycle::

        k = Kernel(cfg)
        await k.startup()
        await k.bus.publish("topic", {...})
        await k.shutdown()
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        history_size = int(get(cfg, "event_bus", "history_size", default=256))
        swallow = bool(get(cfg, "event_bus", "swallow_subscriber_errors", default=True))
        self.bus = EventBus(history_size=history_size, swallow_subscriber_errors=swallow)
        self.plugins: list[BasePlugin] = []
        self._started = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        if self._started:
            raise KernelError("kernel already started")
        logger.info("kernel: starting up")

        plugins_root = Path(get(self.cfg, "plugins", "root", default="plugins"))
        if not plugins_root.is_absolute():
            # Resolve relative to the AURORA_HOME anchor if set.
            home = get(self.cfg, "aurora", "home")
            if home:
                plugins_root = Path(home) / plugins_root
        logger.info("kernel: plugin root = %s", plugins_root)

        enabled = list(get(self.cfg, "plugins", "enabled", default=[]) or [])
        disabled = list(get(self.cfg, "plugins", "disabled", default=[]) or [])
        on_error = str(get(self.cfg, "plugins", "on_error", default="safe"))

        self.plugins = discover_plugins(
            plugins_root,
            enabled=enabled,
            disabled=disabled,
            on_error=on_error,
        )

        # setUp each plugin and wire its topic subscriptions.
        load_errors: list[str] = []
        for plugin in self.plugins:
            ctx = PluginContext(
                name=plugin.name,
                config=self._plugin_config(plugin.name),
                bus=self.bus,
            )
            try:
                await plugin.setup(ctx)
            except Exception as exc:
                msg = f"setup() failed for {plugin.name}: {exc}"
                logger.exception(msg)
                load_errors.append(msg)
                continue
            for topic in plugin.subscribes:
                await self.bus.subscribe(topic, plugin.handle_event)
            logger.info(
                "kernel: %s ready (capabilities=%s, subscribes=%s)",
                plugin.name,
                plugin.capabilities,
                plugin.subscribes,
            )

        if load_errors and on_error == "strict":
            await self.shutdown()
            raise KernelError("; ".join(load_errors))

        self._started = True
        logger.info(
            "kernel: up with %d plugin(s) — %s",
            len(self.plugins),
            ", ".join(p.name for p in self.plugins) or "<none>",
        )

        # Notify plugins that all peers are loaded and subscriptions are active
        await self.bus.publish("kernel.ready", {})

    async def shutdown(self) -> None:
        if not self._started and not self.plugins:
            return
        logger.info("kernel: shutting down")
        # Reverse-load order: tear down the most-recently-added first.
        for plugin in reversed(self.plugins):
            try:
                await plugin.shutdown()
            except Exception:
                logger.exception("shutdown() raised for %s", plugin.name)
        self.plugins.clear()
        self._started = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        return {
            "plugins": [p.describe() for p in self.plugins],
            "subscribers": self.bus.subscriber_count(),
            "topics": self.bus.topics(),
            "history_size": len(self.bus.history()),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _plugin_config(self, plugin_name: str) -> dict[str, Any]:
        """Return the plugin config merged with the root configuration."""
        merged = dict(self.cfg)
        sub = get(self.cfg, "plugins", plugin_name)
        if isinstance(sub, dict):
            merged.update(sub)
        return merged
