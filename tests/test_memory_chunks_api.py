"""Integration tests for the Memory Chunks (RAG & AST) Backend API."""

from __future__ import annotations

from pathlib import Path
import pytest
import pytest_asyncio

from aurora_core.config import load_config
from aurora_core.kernel import Kernel
from aurora_core.transport import wire_app
from aurora_core.transport.http import app
from core_logic.memory_inspector import clear_memory_chunks

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _REPO_ROOT / "config" / "aurora.yaml"


@pytest_asyncio.fixture
async def kernel() -> Kernel:
    cfg = load_config(_CONFIG_PATH)
    k = Kernel(cfg)
    await k.startup()
    try:
        yield k
    finally:
        await k.shutdown()


@pytest_asyncio.fixture
async def client(kernel: Kernel):
    wire_app(kernel)
    async with app.test_client() as c:
        yield c


@pytest.mark.asyncio
async def test_get_memory_chunks_default(client):
    """Test GET /memory/chunks returns default RAG and AST chunks."""
    clear_memory_chunks()
    res = await client.get("/memory/chunks")
    assert res.status_code == 200

    data = await res.get_json()
    assert data["ok"] is True
    assert "summary" in data
    assert "rag" in data
    assert "ast" in data
    assert isinstance(data["rag"]["loaded_chunks"], list)
    assert isinstance(data["ast"]["symbol_chunks"], list)


@pytest.mark.asyncio
async def test_get_api_memory_chunks_alias(client):
    """Test alias GET /api/memory/chunks works identically."""
    res = await client.get("/api/memory/chunks")
    assert res.status_code == 200
    data = await res.get_json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_get_memory_chunks_with_source(client, tmp_path: Path):
    """Test on-demand document loading via query parameter."""
    doc_file = tmp_path / "research_notes.md"
    doc_file.write_text("# Section 1\nAurora is a local AI assistant.\n\n# Section 2\nIt supports tools.", encoding="utf-8")

    res = await client.get(f"/memory/chunks?source={doc_file}&chunk_size=100&chunk_overlap=20")
    assert res.status_code == 200

    data = await res.get_json()
    assert data["ok"] is True
    rag = data["rag"]
    assert rag["count"] >= 1
    assert any("research_notes.md" in c["chunk_id"] for c in rag["loaded_chunks"])
    assert any("Aurora is a local AI assistant" in c["text"] for c in rag["loaded_chunks"])


@pytest.mark.asyncio
async def test_get_memory_chunks_with_code_path(client):
    """Test on-demand AST inspection via code_path query parameter."""
    res = await client.get("/memory/chunks?code_path=core_logic/orchestrator.py")
    assert res.status_code == 200

    data = await res.get_json()
    assert data["ok"] is True
    ast_data = data["ast"]
    assert ast_data["count"] >= 1
    # Check that Orchestrator class symbol was extracted into memory chunks
    classes = [c for c in ast_data["symbol_chunks"] if c.get("type") == "class"]
    assert any(c["name"] == "Orchestrator" for c in classes)


@pytest.mark.asyncio
async def test_post_memory_chunks(client, tmp_path: Path):
    """Test POST /memory/chunks with JSON payload."""
    test_doc = tmp_path / "project_specs.txt"
    test_doc.write_text("Specification for memory inspector endpoint.", encoding="utf-8")

    payload = {
        "source": str(test_doc),
        "code_path": "core_logic/prompt_builder.py",
        "chunk_size": 200,
        "chunk_overlap": 30,
    }

    res = await client.post("/memory/chunks", json=payload)
    assert res.status_code == 200

    data = await res.get_json()
    assert data["ok"] is True
    assert data["summary"]["total_rag_chunks"] >= 1
    assert data["summary"]["total_ast_symbols"] >= 1
