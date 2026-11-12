"""Hello-world plugin — Phase 0 verification gate.

This plugin exists to prove the kernel's contract works end-to-end:

  * It declares capabilities and subscribes to ``test.event``.
  * On setup, it logs a banner so we can see it loaded.
  * On ``test.event``, it logs receipt with the payload.

If ``python -m aurora_core`` prints a ``received test.event`` line,
Phase 0 is green. See ``implementation_plan.md`` Phase 0 verify step.
"""

from __future__ import annotations

from aurora_core.plugin_base import BasePlugin, PluginContext


class HelloWorldPlugin(BasePlugin):
    name = "hello_world"
    version = "0.1.0"
    capabilities = ["test.echo"]
    requires: list[str] = []
    subscribes = ["test.event"]

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        ctx.logger.info("hello_world plugin: ready")

    async def shutdown(self) -> None:
        if self._ctx is not None:
            self._ctx.logger.info("hello_world plugin: bye")
        self._ctx = None

    async def handle_event(self, topic: str, payload: dict) -> None:
        # We only subscribed to test.event, so this is just for clarity.
        if topic == "test.event":
            assert self._ctx is not None
            self._ctx.logger.info(
                "received test.event payload=%s", payload
            )


# Explicit symbol for the registry to find unambiguously.
PLUGIN = HelloWorldPlugin
