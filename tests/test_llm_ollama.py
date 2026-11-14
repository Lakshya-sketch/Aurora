"""Unit & integration tests for OllamaLLMPlugin."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio

from aurora_core.event_bus import EventBus
from aurora_core.plugin_base import PluginContext
from plugins.llm_ollama.plugin import OllamaPlugin


@pytest_asyncio.fixture
async def bus() -> EventBus:
    return EventBus(history_size=64, swallow_subscriber_errors=True)


@pytest.mark.asyncio
async def test_ollama_plugin_setup_and_describe(bus: EventBus) -> None:
    plugin = OllamaPlugin()
    ctx = PluginContext(name="llm_ollama", config={"llm": {"base_url": "http://127.0.0.1:11434"}}, bus=bus)

    await plugin.setup(ctx)
    desc = plugin.describe()

    assert desc["name"] == "llm_ollama"
    assert "llm.chat" in desc["capabilities"]
    assert "llm.generate" in desc["subscribes"]

    await plugin.shutdown()


@pytest.mark.asyncio
async def test_ollama_plugin_fallback_when_offline(bus: EventBus) -> None:
    """When Ollama service is unreachable, plugin emits fallback token and chat.complete."""
    plugin = OllamaPlugin()

    # Use a non-existent port to force immediate connection error
    ctx = PluginContext(name="llm_ollama", config={"llm": {"base_url": "http://127.0.0.1:59999", "timeout_s": 1.0}}, bus=bus)
    await plugin.setup(ctx)

    received_tokens: list[str] = []
    completed = False

    async def token_handler(topic: str, payload: dict[str, Any]) -> None:
        received_tokens.append(payload.get("token", ""))

    async def complete_handler(topic: str, payload: dict[str, Any]) -> None:
        nonlocal completed
        completed = True

    await bus.subscribe("llm.token", token_handler)
    await bus.subscribe("chat.complete", complete_handler)

    await plugin.handle_event(
        "llm.generate",
        {
            "session_id": "test_sess",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert len(received_tokens) > 0
    assert "Ollama" in received_tokens[0] or "Unable to connect" in received_tokens[0]
    assert completed is True

    await plugin.shutdown()


@pytest.mark.asyncio
async def test_ollama_receives_tool_schemas_via_event_bus(bus: EventBus) -> None:
    """Verify Rule 2: tool schemas are discovered via EventBus topics without importing tools_builtin."""
    plugin = OllamaPlugin()
    ctx = PluginContext(name="llm_ollama", config={}, bus=bus)
    await plugin.setup(ctx)

    # Initially empty before any tools plugin advertises
    assert len(plugin._get_tool_schemas()) == 0

    # Simulate tools_builtin publishing its catalog over EventBus
    sample_schemas = [
        {
            "type": "function",
            "function": {"name": "test_calculator", "description": "math eval"},
        }
    ]
    await plugin.handle_event("tools.catalog", {"schemas": sample_schemas})

    cached = plugin._get_tool_schemas()
    assert len(cached) == 1
    assert cached[0]["function"]["name"] == "test_calculator"

    await plugin.shutdown()


@pytest.mark.asyncio
async def test_ollama_context_traceback_memory_and_vision(bus: EventBus, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that memory preview lines and vision subagent context are included in the stderr trace."""
    import io
    import json
    import sys

    plugin = OllamaPlugin()
    ctx = PluginContext(name="llm_ollama", config={"llm": {"base_url": "http://127.0.0.1:59999", "timeout_s": 0.5}}, bus=bus)
    await plugin.setup(ctx)

    stderr_buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr_buf)

    sample_context = (
        "=== CONTEXT / REFERENCE KNOWLEDGE ===\n"
        "=== LONG-TERM MEMORY & RECALLED FACTS (2 memories) ===\n\n"
        "[Memory 1 | Scope: conversation | Session: sess_001 | Score: 1.000]\n"
        "Dataset for fake news detection research.\n\n"
        "[Memory 2 | Scope: conversation | Session: sess_001 | Score: 0.850]\n"
        "User prefers PyTorch for training models.\n\n"
        "=== ATTACHED IMAGE FILE: sample.jpeg ===\n"
        "File: E:\\test\\sample.jpeg\n"
        "Size: 2048 bytes\n"
        "NOTE: Call inspect_image tool to analyze this file.\n"
        "=== END OF ATTACHED IMAGE ===\n"
        "=== END CONTEXT ==="
    )

    messages = [
        {"role": "system", "content": f"Instructions\n\n{sample_context}"},
        {
            "role": "tool",
            "content": json.dumps({
                "ok": True,
                "data": {
                    "filename": "sample.jpeg",
                    "model_used": "qwen3-vl:4b",
                    "description_and_ocr": "Detected Python code and architecture diagram.",
                },
            }),
        },
        {"role": "user", "content": "Explain the image."},
    ]

    await plugin.handle_event(
        "llm.generate",
        {
            "session_id": "test_trace_sess",
            "messages": messages,
        },
    )

    output = stderr_buf.getvalue()
    assert "[CONTEXT TRACEBACK] Injected Reference Knowledge & Vision Context:" in output
    assert "Dataset for fake news detection research." in output
    assert "User prefers PyTorch for training models." in output
    assert "sample.jpeg" in output
    assert "VISION SUBAGENT OCR & CONTEXT: sample.jpeg (qwen3-vl:4b)" in output
    assert "Detected Python code and architecture diagram." in output

    await plugin.shutdown()


@pytest.mark.asyncio
async def test_ollama_gpu_options_and_dynamic_model_refresh(bus: EventBus) -> None:
    """Verify forced GPU offloading, num_ctx, and dynamic model refresh with auto-fallback."""
    plugin = OllamaPlugin()
    ctx = PluginContext(
        name="llm_ollama",
        config={
            "llm": {
                "base_url": "http://127.0.0.1:11434",
                "num_gpu": 99,
                "num_ctx": 4096,
                "default_model": "qwen2.5:3b-instruct-q4_K_M",
            }
        },
        bus=bus,
    )
    await plugin.setup(ctx)

    assert plugin.num_gpu == 99
    assert plugin.num_ctx == 4096

    # Mock live fetch returning 2 installed models (simulating Ollama API /api/tags)
    async def mock_fetch() -> tuple[bool, list[str]]:
        return True, ["llama3:8b", "mistral:7b"]

    plugin._fetch_models_from_ollama = mock_fetch  # type: ignore[assignment]

    # Refresh models dynamically
    models = await plugin.refresh_installed_models()
    assert models == ["llama3:8b", "mistral:7b"]
    # Because qwen2.5 is not in installed models, active_model should auto-fallback to first usable
    assert plugin.active_model == "llama3:8b"

    # Mock zero models installed in Ollama
    async def mock_empty_fetch() -> tuple[bool, list[str]]:
        return True, []

    plugin._fetch_models_from_ollama = mock_empty_fetch  # type: ignore[assignment]
    models = await plugin.refresh_installed_models()
    assert models == []
    assert plugin.active_model == "None (No models installed)"

    # When sending a prompt with zero models installed, it should emit friendly fallback message
    received_tokens: list[str] = []
    async def token_collector(topic: str, payload: dict[str, Any]) -> None:
        received_tokens.append(payload.get("token", ""))

    await bus.subscribe("llm.token", token_collector)
    await plugin.handle_event(
        "llm.generate",
        {
            "session_id": "test_zero_models",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert len(received_tokens) > 0
    assert "No Ollama models are installed" in received_tokens[0]

    await plugin.shutdown()



