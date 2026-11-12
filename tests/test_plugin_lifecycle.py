"""Tests for the plugin lifecycle: discovery, setup, subscribe, shutdown.

We exercise ``aurora_core.kernel.Kernel`` against a tiny on-the-fly
plugin tree under ``tmp_path`` so the real filesystem-discovery
codepath runs end-to-end without depending on the bundled
``plugins/hello_world`` folder.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aurora_core.kernel import Kernel


PLUGIN_A_SRC = textwrap.dedent(
    """
    from aurora_core.plugin_base import BasePlugin, PluginContext

    class AlphaPlugin(BasePlugin):
        name = "alpha"
        version = "0.1.0"
        capabilities = ["alpha.do"]
        subscribes = ["alpha.fire"]

        def __init__(self):
            super().__init__()
            self.received = []
            self.setup_called = False
            self.shutdown_called = False

        async def setup(self, ctx):
            self.setup_called = True
            self._ctx = ctx

        async def shutdown(self):
            self.shutdown_called = True
            self._ctx = None

        async def handle_event(self, topic, payload):
            if topic == "alpha.fire":
                self.received.append(payload)

    PLUGIN = AlphaPlugin
    """
).lstrip()


PLUGIN_B_SRC = textwrap.dedent(
    """
    from aurora_core.plugin_base import BasePlugin, PluginContext

    class BetaPlugin(BasePlugin):
        name = "beta"
        version = "0.1.0"
        capabilities = ["beta.do"]
        requires = ["alpha.do"]    # not enforced yet, but documented
        subscribes = []

        def __init__(self):
            super().__init__()
            self.setup_called = False
            self.shutdown_called = False

        async def setup(self, ctx):
            self.setup_called = True
            self._ctx = ctx

        async def shutdown(self):
            self.shutdown_called = True

    PLUGIN = BetaPlugin
    """
).lstrip()


def _write_plugin(plugins_dir: Path, name: str, source: str) -> None:
    pkg = plugins_dir / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "plugin.py").write_text(source, encoding="utf-8")


def _make_cfg(plugins_dir: Path) -> dict:
    return {
        "event_bus": {"history_size": 16, "swallow_subscriber_errors": True},
        "plugins": {
            "root": str(plugins_dir),
            "enabled": [],
            "disabled": [],
            "on_error": "safe",
        },
    }


async def test_kernel_discovers_and_starts_two_plugins(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "alpha", PLUGIN_A_SRC)
    _write_plugin(plugins_dir, "beta", PLUGIN_B_SRC)

    kernel = Kernel(_make_cfg(plugins_dir))
    await kernel.startup()
    try:
        names = {p.name for p in kernel.plugins}
        assert names == {"alpha", "beta"}
        for p in kernel.plugins:
            assert p.setup_called is True
            assert p.shutdown_called is False  # not yet
    finally:
        await kernel.shutdown()


async def test_kernel_publishes_event_to_plugin_handler(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "alpha", PLUGIN_A_SRC)

    kernel = Kernel(_make_cfg(plugins_dir))
    await kernel.startup()
    try:
        await kernel.bus.publish("alpha.fire", {"k": 7})
        # The handler is dispatched via asyncio.gather in publish; it
        # has already awaited by the time publish returns, so we can
        # read state immediately.
        alpha = kernel.plugins[0]
        assert alpha.received == [{"k": 7}]
    finally:
        await kernel.shutdown()


async def test_shutdown_calls_every_plugin(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "alpha", PLUGIN_A_SRC)
    _write_plugin(plugins_dir, "beta", PLUGIN_B_SRC)

    kernel = Kernel(_make_cfg(plugins_dir))
    await kernel.startup()
    await kernel.shutdown()

    for p in kernel.plugins:
        assert p.shutdown_called is True
    # And a second shutdown is a no-op, not an error.
    await kernel.shutdown()


async def test_disabled_plugin_is_skipped(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "alpha", PLUGIN_A_SRC)
    _write_plugin(plugins_dir, "beta", PLUGIN_B_SRC)

    cfg = _make_cfg(plugins_dir)
    cfg["plugins"]["disabled"] = ["beta"]

    kernel = Kernel(cfg)
    await kernel.startup()
    try:
        names = {p.name for p in kernel.plugins}
        assert names == {"alpha"}
    finally:
        await kernel.shutdown()


async def test_enabled_list_limits_discovery(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "alpha", PLUGIN_A_SRC)
    _write_plugin(plugins_dir, "beta", PLUGIN_B_SRC)

    cfg = _make_cfg(plugins_dir)
    cfg["plugins"]["enabled"] = ["alpha"]

    kernel = Kernel(cfg)
    await kernel.startup()
    try:
        names = {p.name for p in kernel.plugins}
        assert names == {"alpha"}
    finally:
        await kernel.shutdown()


async def test_setup_failure_is_logged_and_other_plugins_still_load(tmp_path: Path) -> None:
    """A broken plugin must not prevent the kernel from coming up (safe mode)."""
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "alpha", PLUGIN_A_SRC)
    bad_src = textwrap.dedent(
        """
        from aurora_core.plugin_base import BasePlugin, PluginContext

        class BadPlugin(BasePlugin):
            name = "bad"
            version = "0.1.0"
            async def setup(self, ctx):
                raise RuntimeError("intentional failure")
            async def shutdown(self): pass

        PLUGIN = BadPlugin
        """
    ).lstrip()
    _write_plugin(plugins_dir, "bad", bad_src)

    kernel = Kernel(_make_cfg(plugins_dir))
    await kernel.startup()  # must not raise
    try:
        names = {p.name for p in kernel.plugins}
        assert "alpha" in names
    finally:
        await kernel.shutdown()


async def test_strict_mode_raises_on_bad_plugin(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    bad_src = textwrap.dedent(
        """
        from aurora_core.plugin_base import BasePlugin, PluginContext
        class BadPlugin(BasePlugin):
            name = "bad"
            async def setup(self, ctx: PluginContext):
                raise RuntimeError("intentional failure")
            async def shutdown(self): pass
        PLUGIN = BadPlugin
        """
    ).lstrip()
    _write_plugin(plugins_dir, "bad", bad_src)

    cfg = _make_cfg(plugins_dir)
    cfg["plugins"]["on_error"] = "strict"

    kernel = Kernel(cfg)
    with pytest.raises(Exception):
        await kernel.startup()
    await kernel.shutdown()
