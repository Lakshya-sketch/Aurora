"""Unit tests for Vision tools, OCR pipeline, and Option 2 Vision Subagent."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from aurora_core.event_bus import EventBus
from core_logic.orchestrator import Orchestrator
from plugins.tools_builtin.handlers.vision import capture_screen, handle as vision_handle


@pytest_asyncio.fixture
async def bus() -> EventBus:
    return EventBus(history_size=64, swallow_subscriber_errors=True)


@pytest.mark.asyncio
async def test_capture_screen_returns_valid_data(tmp_path: Path) -> None:
    test_img = tmp_path / "custom_screen.png"
    out_path, w, h, b64_str = capture_screen(save_path=test_img)

    assert out_path.is_file()
    assert out_path == test_img
    assert w > 0 and h > 0
    assert len(b64_str) > 0
    # Verify valid base64
    decoded = base64.b64decode(b64_str)
    assert len(decoded) == out_path.stat().st_size


@pytest.mark.asyncio
async def test_vision_handler_take_screenshot(tmp_path: Path) -> None:
    res = await vision_handle({"action": "take_screenshot", "path": str(tmp_path / "screen.png")})
    assert res["ok"] is True
    assert res["data"] is not None
    assert "path" in res["data"]
    assert "images" in res["data"]
    assert len(res["data"]["images"]) == 1


@pytest.mark.asyncio
async def test_vision_handler_read_screen_text(tmp_path: Path) -> None:
    img_file = tmp_path / "sample.jpg"
    img_file.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)  # fake JPEG header

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {
        "message": {"content": "Detected Text: Login Screen Username Password"}
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await vision_handle({
            "action": "read_screen_text",
            "image_path": str(img_file),
            "model": "qwen3-vl:4b-instruct-q4_K_M",
        })

    assert res["ok"] is True
    assert res["data"]["screen_text"] == "Detected Text: Login Screen Username Password"
    assert res["data"]["model_used"] == "qwen3-vl:4b-instruct-q4_K_M"
    # Verify instant VRAM offload flag
    assert mock_post.call_args[1]["json"]["keep_alive"] == 0


@pytest.mark.asyncio
async def test_vision_handler_inspect_image(tmp_path: Path) -> None:
    img_file = tmp_path / "code_screenshot.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {
        "message": {"content": "VS Code editor: def forward(self, x): return x + 1"}
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await vision_handle({
            "action": "inspect_image",
            "image_path": str(img_file),
            "prompt": "Transcribe the code in the editor",
        })

    assert res["ok"] is True
    assert "description_and_ocr" in res["data"]
    assert "def forward" in res["data"]["description_and_ocr"]
    assert res["data"]["filename"] == "code_screenshot.png"
    assert mock_post.call_args[1]["json"]["keep_alive"] == 0


@pytest.mark.asyncio
async def test_orchestrator_processes_image_attachment(bus: EventBus, tmp_path: Path) -> None:
    img_file = tmp_path / "photo.jpg"
    img_bytes = b"fake-jpeg-image-bytes-data-12345"
    img_file.write_bytes(img_bytes)

    orchestrator = Orchestrator(bus=bus)
    context_str, images_b64 = await orchestrator._process_attachments([img_file])

    assert "photo.jpg" in context_str
    assert "ATTACHED IMAGE FILE" in context_str
    assert "inspect_image" in context_str
    assert len(images_b64) == 1
    assert base64.b64decode(images_b64[0]) == img_bytes


@pytest.mark.asyncio
async def test_orchestrator_preserves_main_model_with_inspect_tool(bus: EventBus, tmp_path: Path) -> None:
    img_file = tmp_path / "chart.png"
    img_file.write_bytes(b"chart-image-data")

    orchestrator = Orchestrator(bus=bus)
    generated_events: list[dict[str, Any]] = []

    async def generate_handler(topic: str, payload: dict[str, Any]) -> None:
        generated_events.append(payload)

    await bus.subscribe("llm.generate", generate_handler)

    await orchestrator.handle_user_message(
        "user.message",
        {
            "session_id": "sess_vision",
            "text": f"Explain the bug in this screenshot: {img_file}",
            "model": "qwen3.5:4b-q4_K_M",
        },
    )

    assert len(generated_events) == 1
    event = generated_events[0]
    # In Option 2: The primary model is preserved (not overridden to VLM)
    assert event["model"] == "qwen3.5:4b-q4_K_M"
    # System context contains the inspect_image tool guidance
    system_content = event["messages"][0]["content"]
    assert "inspect_image" in system_content
    assert "chart.png" in system_content
