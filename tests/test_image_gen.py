"""Tests for image generation tool handler with Fooocus API and HTTP endpoints."""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from PIL import Image

from aurora_core.event_bus import EventBus
from aurora_core.kernel import Kernel
from aurora_core.plugin_base import PluginContext
from plugins.tools_builtin.handlers.image_gen import (
    get_active_model,
    get_available_models,
    handle as image_gen_handle,
    set_active_model,
)
from plugins.tools_builtin.plugin import ToolsBuiltinPlugin


@pytest_asyncio.fixture
async def bus() -> EventBus:
    return EventBus(history_size=64, swallow_subscriber_errors=True)


@pytest.mark.asyncio
async def test_image_gen_handler_creates_valid_png() -> None:
    """Test fallback card creation when Fooocus is offline."""
    res = await image_gen_handle({
        "prompt": "A glowing cybernetic tree in the rain",
        "style": "photorealistic",
    })

    assert res["ok"] is True
    assert res["data"] is not None
    data = res["data"]
    assert "image_path" in data
    assert "filename" in data
    assert data["filename"].endswith(".png")
    assert data["engine"] == "Fooocus API"

    img_path = Path(data["image_path"])
    try:
        assert img_path.exists()
        assert img_path.stat().st_size > 0
        with Image.open(img_path) as im:
            assert im.format == "PNG"
            assert im.size[0] > 0 and im.size[1] > 0
    finally:
        img_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_image_gen_fooocus_mocked_success() -> None:
    """Test image generation when Fooocus API returns base64 image data."""
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color=(0, 200, 100)).save(buf, format="PNG")
    dummy_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    from unittest.mock import MagicMock
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"base64": dummy_b64, "finish_reason": "SUCCESS"}]

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        res = await image_gen_handle({
            "prompt": "A serene Japanese zen garden with sakura blossoms",
            "style": "concept art",
            "model": "fooocus-quality",
        })

        assert res["ok"] is True
        data = res["data"]
        assert data["fooocus_connected"] is True
        assert data["model_used"] == "fooocus-quality"
        img_path = Path(data["image_path"])
        try:
            assert img_path.exists()
            with Image.open(img_path) as im:
                assert im.size == (64, 64)
        finally:
            img_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_image_gen_model_switching() -> None:
    """Test switching between Fooocus performance profiles."""
    models = get_available_models()
    assert len(models) >= 3
    ids = [m["id"] for m in models]
    assert "fooocus-speed" in ids
    assert "fooocus-quality" in ids
    assert "fooocus-anime" in ids

    # Test valid switch
    assert set_active_model("fooocus-quality") is True
    assert get_active_model() == "fooocus-quality"

    # Test invalid switch
    assert set_active_model("nonexistent-model-xyz") is False
    assert get_active_model() == "fooocus-quality"

    # Reset back to default
    set_active_model("fooocus-speed")


@pytest.mark.asyncio
async def test_image_gen_eventbus_execution(bus: EventBus) -> None:
    """Test generate_image tool dispatch through the Aurora EventBus."""
    plugin = ToolsBuiltinPlugin()
    ctx = PluginContext(name="tools_builtin", config={}, bus=bus)
    await plugin.setup(ctx)

    # Check that generate_image is advertised in catalog
    schemas = plugin.get_tool_schemas()
    gen_schema = next((s for s in schemas if s.get("function", {}).get("name") == "generate_image"), None)
    assert gen_schema is not None
    assert "prompt" in gen_schema["function"]["parameters"]["properties"]

    # Test execution via tool.execute
    results: list[dict[str, Any]] = []

    async def result_handler(topic: str, payload: dict[str, Any]) -> None:
        results.append(payload)

    await bus.subscribe("tool.result", result_handler)

    await plugin.handle_event(
        "tool.execute",
        {
            "call_id": "call_img_001",
            "session_id": "sess_img",
            "tool": "generate_image",
            "args": {"prompt": "A sunset over mountains", "style": "digital art"},
        },
    )

    assert len(results) == 1
    res = results[0]
    assert res["call_id"] == "call_img_001"
    assert res["ok"] is True
    assert "image_path" in res["data"]
    assert res["data"]["url"].startswith("/generated/")

    Path(res["data"]["image_path"]).unlink(missing_ok=True)
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_image_model_http_endpoints(bus: EventBus) -> None:
    """Test HTTP endpoints for Fooocus image models and static file serving."""
    from aurora_core.kernel import Kernel
    from aurora_core.transport import wire_app
    from aurora_core.transport.http import app

    cfg = {"aurora": {"home": "."}, "plugins": {"enabled": ["tools_builtin"]}}
    kernel = Kernel(cfg)
    plugin = ToolsBuiltinPlugin()
    ctx = PluginContext(name="tools_builtin", config={}, bus=kernel.bus)
    await plugin.setup(ctx)
    kernel.plugins.append(plugin)
    wire_app(kernel)

    client = app.test_client()

    # GET /image_models
    res = await client.get("/image_models")
    assert res.status_code == 200
    data = await res.get_json()
    assert "models" in data
    assert "active" in data
    assert len(data["models"]) >= 3

    # POST /image_model
    res_post = await client.post("/image_model", json={"model": "fooocus-quality"})
    assert res_post.status_code == 200
    data_post = await res_post.get_json()
    assert data_post["active"] == "fooocus-quality"

    # Reset back to fooocus-speed
    await client.post("/image_model", json={"model": "fooocus-speed"})

    # Test GET /generated/<filename> endpoint
    gen_dir = Path(__file__).resolve().parent.parent / "data" / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    test_img = gen_dir / "test_serve.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\nfakeimagecontent")

    try:
        serve_res = await client.get("/generated/test_serve.png")
        assert serve_res.status_code == 200
        assert serve_res.headers.get("Access-Control-Allow-Origin") == "*"
        assert "image/png" in serve_res.headers.get("content-type", "")
        content = await serve_res.get_data()
        assert len(content) > 0

        # Missing file returns recovery placeholder PNG with 200 so UI <img> tags never crash
        missing_res = await client.get("/generated/does_not_exist_xyz.png")
        assert missing_res.status_code == 200
        assert "image/png" in missing_res.headers.get("content-type", "")
        assert len(await missing_res.get_data()) > 0
    finally:
        test_img.unlink(missing_ok=True)

    await plugin.shutdown()
