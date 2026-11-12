"""Comprehensive Unit & Integration Tests for Phase 4.2 Vector RAG.

Tests:
1. `VectorStore`: Ingestion, persistence, cosine similarity retrieval, metadata filtering, deletion.
2. `ContextBuilder`: Formats RAG chunks & AST symbols into clean system prompt context.
3. `RagChromaPlugin`: BasePlugin lifecycle, EventBus pub/sub (`rag.index`, `rag.search`),
   `tools.catalog` advertising, and `rag_search` tool execution.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from aurora_core.event_bus import EventBus
from aurora_core.plugin_base import PluginContext
from core_logic.context_builder import ContextBuilder
from plugins.rag_chroma.document_loader import DocumentChunk
from plugins.rag_chroma.plugin import RagChromaPlugin
from plugins.rag_chroma.store import VectorRecord, VectorStore


# ---------------------------------------------------------------------------
# 1. VectorStore Tests
# ---------------------------------------------------------------------------

class TestVectorStore:
    def test_store_add_and_count(self, tmp_path: Path):
        store = VectorStore(persist_dir=tmp_path / "chroma")
        chunks = [
            DocumentChunk(chunk_id="c1", text="Python is an interpreted language", metadata={"filename": "doc1.txt"}),
            DocumentChunk(chunk_id="c2", text="Aurora uses an event bus architecture", metadata={"filename": "doc2.txt"}),
        ]
        # Synthetic unit vectors
        emb1 = [1.0, 0.0, 0.0]
        emb2 = [0.0, 1.0, 0.0]

        added = store.add(chunks, [emb1, emb2])
        assert added == 2
        assert store.count() == 2

        rec1 = store.get("c1")
        assert rec1 is not None
        assert rec1["text"] == "Python is an interpreted language"
        assert rec1["metadata"]["filename"] == "doc1.txt"

    def test_store_similarity_search(self, tmp_path: Path):
        store = VectorStore(persist_dir=tmp_path / "chroma")
        chunks = [
            {"chunk_id": "c1", "text": "Microkernel architecture", "metadata": {"topic": "arch"}},
            {"chunk_id": "c2", "text": "Neural network embeddings", "metadata": {"topic": "ai"}},
            {"chunk_id": "c3", "text": "Cooking apple pie recipe", "metadata": {"topic": "food"}},
        ]
        # 3D embeddings
        embs = [
            [0.9, 0.1, 0.0],
            [0.1, 0.9, 0.0],
            [0.0, 0.0, 1.0],
        ]
        store.add(chunks, embs)

        # Query close to "arch"
        query_vec = [0.95, 0.05, 0.0]
        results = store.similarity_search(query_vec, k=2)

        assert len(results) == 2
        assert results[0]["chunk_id"] == "c1"
        assert results[0]["score"] > 0.9
        assert results[0]["metadata"]["topic"] == "arch"

    def test_store_metadata_filter(self, tmp_path: Path):
        store = VectorStore(persist_dir=tmp_path / "chroma")
        chunks = [
            {"chunk_id": "c1", "text": "Doc A1", "metadata": {"filename": "a.txt"}},
            {"chunk_id": "c2", "text": "Doc A2", "metadata": {"filename": "a.txt"}},
            {"chunk_id": "c3", "text": "Doc B1", "metadata": {"filename": "b.txt"}},
        ]
        embs = [[1.0, 0.0], [0.9, 0.1], [0.95, 0.05]]
        store.add(chunks, embs)

        results = store.similarity_search([1.0, 0.0], k=5, filter={"filename": "a.txt"})
        assert len(results) == 2
        assert all(r["metadata"]["filename"] == "a.txt" for r in results)

    def test_store_session_scoped_search(self, tmp_path: Path):
        store = VectorStore(persist_dir=tmp_path / "chroma")
        chunks = [
            {"chunk_id": "s1", "text": "Session 1 helper function", "metadata": {"session_id": "sess_1"}},
            {"chunk_id": "s2", "text": "Session 2 Indian History", "metadata": {"session_id": "sess_2"}},
            {"chunk_id": "g1", "text": "Global handbook", "metadata": {"session_id": "global"}},
        ]
        embs = [[1.0, 0.0], [0.95, 0.05], [0.9, 0.1]]
        store.add(chunks, embs)

        # Scoped to sess_1: should retrieve s1 and global g1, NOT s2
        res1 = store.similarity_search([1.0, 0.0], k=5, session_id="sess_1")
        chunk_ids1 = [r["chunk_id"] for r in res1]
        assert "s1" in chunk_ids1
        assert "g1" in chunk_ids1
        assert "s2" not in chunk_ids1

        # Scoped to sess_2: should retrieve s2 and global g1, NOT s1
        res2 = store.similarity_search([1.0, 0.0], k=5, session_id="sess_2")
        chunk_ids2 = [r["chunk_id"] for r in res2]
        assert "s2" in chunk_ids2
        assert "g1" in chunk_ids2
        assert "s1" not in chunk_ids2

        # Brand new empty session: should only get global, never other chat docs
        res3 = store.similarity_search([1.0, 0.0], k=5, session_id="sess_empty")
        chunk_ids3 = [r["chunk_id"] for r in res3]
        assert "g1" in chunk_ids3
        assert "s1" not in chunk_ids3
        assert "s2" not in chunk_ids3

    def test_store_persistence_and_reload(self, tmp_path: Path):
        persist_dir = tmp_path / "chroma_persist"
        store1 = VectorStore(persist_dir=persist_dir)
        store1.add(
            [{"chunk_id": "p1", "text": "Persistent knowledge", "metadata": {"source": "manual"}}],
            [[0.5, 0.5, 0.5]],
        )
        assert (persist_dir / "vector_store.json").exists()

        # Reload in a new instance
        store2 = VectorStore(persist_dir=persist_dir)
        assert store2.count() == 1
        assert store2.get("p1")["text"] == "Persistent knowledge"

    def test_store_delete_and_clear(self, tmp_path: Path):
        store = VectorStore(persist_dir=tmp_path / "chroma")
        store.add(
            [{"chunk_id": "d1", "text": "t1"}, {"chunk_id": "d2", "text": "t2"}],
            [[1.0, 0.0], [0.0, 1.0]],
        )
        assert store.count() == 2

        store.delete(["d1"])
        assert store.count() == 1
        assert store.get("d1") is None
        assert store.get("d2") is not None

        store.clear()
        assert store.count() == 0


# ---------------------------------------------------------------------------
# 2. ContextBuilder Tests
# ---------------------------------------------------------------------------

class TestContextBuilder:
    def test_format_rag_chunks(self):
        builder = ContextBuilder()
        chunks = [
            {
                "chunk_id": "c1",
                "text": "Aurora is a local desktop AI assistant.",
                "metadata": {"filename": "README.md", "page_number": 1},
                "score": 0.954,
            },
            {
                "chunk_id": "c2",
                "text": "Every capability is a plugin behind BasePlugin.",
                "metadata": {"filename": "architecture.md", "page_number": 2},
                "score": 0.887,
            },
        ]
        context = builder.format_rag_chunks(chunks)

        assert "=== RETRIEVED DOCUMENT CONTEXT (2 chunks) ===" in context
        assert "README.md (Page 1)" in context
        assert "Relevance: 0.954" in context
        assert "architecture.md (Page 2)" in context
        assert "BasePlugin" in context
        assert "=== END OF RETRIEVED DOCUMENT CONTEXT ===" in context

    def test_format_ast_symbols(self):
        builder = ContextBuilder()
        symbols = {
            "file": "calc.py",
            "classes": [
                {"name": "Calculator", "line_start": 1, "line_end": 10, "methods": [{"name": "add", "signature": "add(a, b)"}]}
            ],
            "functions": [
                {"name": "multiply", "signature": "multiply(x, y)", "line_start": 12}
            ],
        }
        ast_ctx = builder.format_ast_symbols(symbols)

        assert "=== CODE STRUCTURE & SYMBOLS (AST) ===" in ast_ctx
        assert "class Calculator (L1-L10): add(a, b)" in ast_ctx
        assert "multiply(x, y) (Line 12)" in ast_ctx

    def test_build_context_combined(self):
        builder = ContextBuilder()
        rag_chunks = [{"text": "Doc text", "metadata": {"filename": "doc.txt"}}]
        ast_syms = {"file": "main.py", "functions": [{"name": "run"}]}

        full_ctx = builder.build_context(rag_chunks=rag_chunks, ast_symbols=ast_syms)
        assert "=== RETRIEVED DOCUMENT CONTEXT" in full_ctx
        assert "=== CODE STRUCTURE & SYMBOLS (AST) ===" in full_ctx


# ---------------------------------------------------------------------------
# 3. RagChromaPlugin Integration & EventBus Tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def bus() -> EventBus:
    return EventBus(history_size=64, swallow_subscriber_errors=True)


@pytest.mark.asyncio
async def test_rag_plugin_setup_and_tool_catalog(bus: EventBus, tmp_path: Path):
    plugin = RagChromaPlugin()
    catalog_events: list[dict[str, Any]] = []

    async def catalog_handler(topic: str, payload: dict[str, Any]) -> None:
        catalog_events.append(payload)

    await bus.subscribe("tools.catalog", catalog_handler)

    ctx = PluginContext(
        name="rag_chroma",
        bus=bus,
        config={"aurora": {"data_dir": str(tmp_path / "data")}},
    )
    await plugin.setup(ctx)

    try:
        assert len(catalog_events) >= 1
        schemas = catalog_events[0]["schemas"]
        tool_names = [s["function"]["name"] for s in schemas]
        assert "rag_search" in tool_names
    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_rag_plugin_index_and_search_events(bus: EventBus, tmp_path: Path):
    doc_path = tmp_path / "aurora_knowledge.txt"
    doc_path.write_text(
        "Aurora v2 features a high-performance vector RAG engine with cosine similarity search.\n"
        "It supports offline fallback embeddings and zero external database dependencies.",
        encoding="utf-8",
    )

    plugin = RagChromaPlugin()
    ctx = PluginContext(
        name="rag_chroma",
        bus=bus,
        config={"aurora": {"data_dir": str(tmp_path / "data")}},
    )
    await plugin.setup(ctx)

    indexed_events: list[dict[str, Any]] = []
    search_events: list[dict[str, Any]] = []

    async def index_handler(topic: str, payload: dict[str, Any]) -> None:
        indexed_events.append(payload)

    async def search_handler(topic: str, payload: dict[str, Any]) -> None:
        search_events.append(payload)

    await bus.subscribe("rag.indexed", index_handler)
    await bus.subscribe("rag.result", search_handler)

    try:
        # 1. Trigger rag.index
        await plugin.handle_event(
            "rag.index",
            {"file_paths": [str(doc_path)], "call_id": "idx_1", "session_id": "test_sess"},
        )

        assert len(indexed_events) == 1
        assert indexed_events[0]["ok"] is True
        assert indexed_events[0]["chunks_count"] > 0

        # 2. Trigger rag.search
        await plugin.handle_event(
            "rag.search",
            {"query": "vector RAG engine cosine similarity", "k": 3, "call_id": "srch_1", "session_id": "test_sess"},
        )

        assert len(search_events) == 1
        res = search_events[0]
        assert res["ok"] is True
        assert len(res["results"]) > 0
        top_match = res["results"][0]
        assert "vector RAG engine" in top_match["text"]
        assert top_match["score"] > 0.0

    finally:
        await plugin.shutdown()


@pytest.mark.asyncio
async def test_rag_plugin_tool_execute(bus: EventBus, tmp_path: Path):
    doc_path = tmp_path / "manual.txt"
    doc_path.write_text("Operating instruction: Turn the blue knob clockwise to start.", encoding="utf-8")

    plugin = RagChromaPlugin()
    ctx = PluginContext(
        name="rag_chroma",
        bus=bus,
        config={"aurora": {"data_dir": str(tmp_path / "data")}},
    )
    await plugin.setup(ctx)

    tool_results: list[dict[str, Any]] = []

    async def result_handler(topic: str, payload: dict[str, Any]) -> None:
        tool_results.append(payload)

    await bus.subscribe("tool.result", result_handler)

    try:
        # Index document first
        await plugin.handle_event("rag.index", {"file_paths": [str(doc_path)]})

        # Execute rag_search tool
        await plugin.handle_event(
            "tool.execute",
            {
                "tool": "rag_search",
                "call_id": "call_knob",
                "session_id": "sess_knob",
                "args": {"query": "blue knob instruction", "k": 2},
            },
        )

        assert len(tool_results) == 1
        result = tool_results[0]
        assert result["tool"] == "rag_search"
        assert result["ok"] is True
        assert len(result["data"]["results"]) > 0
        assert "blue knob clockwise" in result["data"]["results"][0]["text"]

    finally:
        await plugin.shutdown()
