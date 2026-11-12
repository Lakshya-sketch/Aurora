"""Unit tests for Orchestrator."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from aurora_core.event_bus import EventBus
from core_logic.orchestrator import Orchestrator


@pytest_asyncio.fixture
async def bus() -> EventBus:
    return EventBus(history_size=64, swallow_subscriber_errors=True)


@pytest.mark.asyncio
async def test_orchestrator_publishes_llm_generate(bus: EventBus) -> None:
    orchestrator = Orchestrator(bus=bus)
    generated_events: list[dict[str, Any]] = []

    async def generate_handler(topic: str, payload: dict[str, Any]) -> None:
        generated_events.append(payload)

    await bus.subscribe("llm.generate", generate_handler)

    await orchestrator.handle_user_message(
        "user.message",
        {"session_id": "sess_1", "text": "What is Python?"},
    )

    assert len(generated_events) == 1
    event = generated_events[0]
    assert event["session_id"] == "sess_1"
    assert len(event["messages"]) == 2
    assert event["messages"][1]["content"] == "What is Python?"


@pytest.mark.asyncio
async def test_orchestrator_processes_attached_document(bus: EventBus, tmp_path) -> None:
    from core_logic.memory_inspector import get_active_rag_chunks

    doc_file = tmp_path / "notes.txt"
    doc_file.write_text("Aurora v2 is an intelligent desktop companion.\nIt features native RAG and AST code intelligence.", encoding="utf-8")

    orchestrator = Orchestrator(bus=bus)
    generated_events: list[dict[str, Any]] = []

    async def generate_handler(topic: str, payload: dict[str, Any]) -> None:
        generated_events.append(payload)

    await bus.subscribe("llm.generate", generate_handler)

    await orchestrator.handle_user_message(
        "user.message",
        {"session_id": "sess_doc", "text": f"Attach and summarise: {doc_file}"},
    )

    assert len(generated_events) == 1
    system_content = generated_events[0]["messages"][0]["content"]
    user_content = generated_events[0]["messages"][-1]["content"]

    # Verify context was injected cleanly into system message, not polluting user prompt
    assert "=== CONTEXT / REFERENCE KNOWLEDGE ===" in system_content
    assert "notes.txt" in system_content
    assert "Aurora v2 is an intelligent desktop companion" in system_content
    assert "notes.txt" in user_content
    assert "=== ATTACHED DOCUMENT CONTENT" not in user_content

    # Verify memory inspector received the chunks
    chunks = get_active_rag_chunks()
    assert any(c.get("metadata", {}).get("filename") == "notes.txt" for c in chunks)


@pytest.mark.asyncio
async def test_orchestrator_processes_attached_code_ast(bus: EventBus, tmp_path) -> None:
    from core_logic.memory_inspector import get_active_ast_symbols

    py_file = tmp_path / "calc_sample.py"
    py_file.write_text("class Calculator:\n    def add(self, a, b):\n        return a + b\n\ndef multiply(x, y):\n    return x * y\n", encoding="utf-8")

    orchestrator = Orchestrator(bus=bus)
    generated_events: list[dict[str, Any]] = []

    async def generate_handler(topic: str, payload: dict[str, Any]) -> None:
        generated_events.append(payload)

    await bus.subscribe("llm.generate", generate_handler)

    await orchestrator.handle_user_message(
        "user.message",
        {"session_id": "sess_ast", "text": f"Explain this code: {py_file}"},
    )

    assert len(generated_events) == 1
    system_content = generated_events[0]["messages"][0]["content"]
    user_content = generated_events[0]["messages"][-1]["content"]

    # Injected code file context in system context (now includes AST + full source)
    assert "=== ATTACHED CODE FILE: calc_sample.py ===" in system_content
    assert "Class Calculator" in system_content
    assert "multiply" in system_content
    # Source code should also be embedded in context
    assert "def add" in system_content
    assert "=== ATTACHED CODE FILE" not in user_content

    # Verify memory inspector received the AST symbols and RAG chunks
    from core_logic.memory_inspector import get_active_rag_chunks
    ast_symbols = get_active_ast_symbols()
    assert any(s.get("name") == "Calculator" for s in ast_symbols)
    rag_chunks = get_active_rag_chunks()
    assert any("Calculator" in c.get("text", "") for c in rag_chunks)

