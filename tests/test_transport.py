"""Tests for the Quart transport (HTTP + WebSocket).

We bring up a real Kernel against the bundled ``plugins/hello_world``
folder and a real Hypercorn ASGI driver, then exercise the routes
with Quart's built-in ``test_client``. This is integration-level —
no mocks for the bus or the kernel.

These tests assume Phase 0's hello_world plugin is present.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from aurora_core.config import load_config
from aurora_core.kernel import Kernel
from aurora_core.transport import wire_app
from aurora_core.transport.http import app


_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _REPO_ROOT / "config" / "aurora.yaml"


@pytest_asyncio.fixture
async def kernel(tmp_path: Path) -> Kernel:
    """Build a real Kernel against the real plugins/ tree with isolated test database."""
    cfg = load_config(_CONFIG_PATH)
    test_db = str(tmp_path / "test_aurora.db")
    if "aurora" not in cfg:
        cfg["aurora"] = {}
    cfg["aurora"]["db_path"] = test_db
    if "memory" not in cfg:
        cfg["memory"] = {}
    cfg["memory"]["db_path"] = test_db
    k = Kernel(cfg)
    await k.startup()
    try:
        yield k
    finally:
        await k.shutdown()


@pytest_asyncio.fixture
async def client(kernel: Kernel):
    """Quart test client with the app wired to the live kernel."""
    wire_app(kernel)
    # Quart 0.19+: test_client is a context manager for async tests.
    async with app.test_client() as c:
        yield c


async def test_health_returns_200_and_kernel_state(client, kernel: Kernel) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["up"] is True
    assert "hello_world" in body["plugins"]
    assert "test.event" in body["topics"]


async def test_plugins_returns_detailed_list(client, kernel: Kernel) -> None:
    resp = await client.get("/plugins")
    assert resp.status_code == 200
    body = await resp.get_json()
    names = {p["name"] for p in body}
    assert "hello_world" in names
    # Detailed shape per plugin_base.describe()
    for p in body:
        assert {"name", "version", "capabilities", "requires", "subscribes"} <= p.keys()


async def test_config_returns_full_resolved_config(client, kernel: Kernel) -> None:
    resp = await client.get("/config")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert "aurora" in body
    assert "transport" in body
    assert "plugins" in body


async def test_websocket_publishes_to_bus(client, kernel: Kernel) -> None:
    """UI sends publish envelope; bus receives it; hello_world plugin logs receipt.

    We don't inspect logs (that would be brittle) — we assert the bus
    observed the publish by checking that history grew.
    """
    before = len(kernel.bus.history())

    async with app.test_client() as c:
        async with c.websocket("/stream") as ws:
            # Receive the bus event the kernel will fire when we publish
            # from the test side. (hello_world subscribed to test.event
            # AND the WS subscribed to *, so a single publish fans out
            # to both — but we just want to confirm the WS is alive
            # and round-trips.)
            await ws.send_json(
                {"type": "publish", "topic": "test.event", "payload": {"via": "ws"}}
            )
            # Drain a small number of events with a timeout so the
            # test does not hang if the bus is quiet.
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
            except asyncio.TimeoutError:
                msg = None
            # The exact ordering of bus-side history vs WS dispatch
            # depends on asyncio scheduling; the invariant we can
            # always check is that *something* arrived.
            if msg is not None:
                assert msg["type"] in {"event", "pong", "error"}

    # The publish definitely hit the bus.
    after = len(kernel.bus.history())
    assert after > before


async def test_websocket_ping_pong(client, kernel: Kernel) -> None:
    async with app.test_client() as c:
        async with c.websocket("/stream") as ws:
            await ws.send_json({"type": "ping"})
            # Drain until we see the pong (events may arrive first).
            for _ in range(5):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                if msg.get("type") == "pong":
                    break
            else:
                pytest.fail("never received pong")


async def test_websocket_rejects_bad_envelope(client, kernel: Kernel) -> None:
    async with app.test_client() as c:
        async with c.websocket("/stream") as ws:
            await ws.send_json({"type": "publish"})  # missing topic
            for _ in range(5):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                if msg.get("type") == "error":
                    assert "topic" in msg["message"]
                    break
            else:
                pytest.fail("never received error envelope")


async def test_stats_includes_database_storage(client, kernel: Kernel) -> None:
    resp = await client.get("/stats")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert "database" in body
    db = body["database"]
    assert "total_bytes" in db
    assert "total_human" in db
    assert "sqlite_bytes" in db
    assert "vector_bytes" in db
    assert "percent_used" in db

    db_resp = await client.get("/database/stats")
    assert db_resp.status_code == 200
    db_body = await db_resp.get_json()
    assert db_body["total_bytes"] == db["total_bytes"]


async def test_clear_database_endpoint(client, kernel: Kernel) -> None:
    resp = await client.post("/database/clear")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["ok"] is True
    assert "cleaned back to 0" in body["message"]
    assert "database" in body
    assert body["database"]["messages_count"] == 0
    assert body["database"]["memories_count"] == 0


async def test_sessions_endpoints(client, kernel: Kernel) -> None:
    # 1. GET /sessions
    resp = await client.get("/sessions")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert body["ok"] is True
    assert isinstance(body["sessions"], list)

    # 2. GET /sessions/<id>/messages
    resp_msg = await client.get("/sessions/test_sess_404/messages")
    assert resp_msg.status_code == 200
    msg_body = await resp_msg.get_json()
    assert msg_body["ok"] is True
    assert msg_body["messages"] == []


async def test_vlm_model_endpoints(client, kernel: Kernel) -> None:
    resp = await client.get("/stats")
    assert resp.status_code == 200
    body = await resp.get_json()
    assert "vlm_model" in body

    # Missing model field returns 400
    bad_resp = await client.post("/vlm_model", json={})
    assert bad_resp.status_code == 400



