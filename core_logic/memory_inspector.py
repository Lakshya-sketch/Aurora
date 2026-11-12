"""Memory Inspector — Bridges RAG Chunks and AST Code Intelligence for Real-Time Inspection.

Maintains active memory buffers of:
1. Document chunks loaded via RAG (PDF, Markdown, TXT, etc.)
2. Code symbols & structural chunks extracted via AST (Classes, Methods, Signatures)

Provides unified inspection APIs for the backend HTTP transport layer.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("aurora.memory_inspector")

# In-memory storage for active chunks and symbol maps
_ACTIVE_RAG_CHUNKS: list[dict[str, Any]] = []
_ACTIVE_AST_SYMBOLS: list[dict[str, Any]] = []


def register_rag_chunks(chunks: list[Any]) -> None:
    """Register loaded RAG chunks into active memory."""
    global _ACTIVE_RAG_CHUNKS
    for c in chunks:
        if hasattr(c, "to_dict"):
            chunk_dict = c.to_dict()
        elif isinstance(c, dict):
            chunk_dict = c
        else:
            continue

        # Avoid duplicate chunk IDs
        if not any(existing.get("chunk_id") == chunk_dict.get("chunk_id") for existing in _ACTIVE_RAG_CHUNKS):
            _ACTIVE_RAG_CHUNKS.append(chunk_dict)


def clear_memory_inspector() -> None:
    """Clear all active RAG chunks and AST symbols from in-memory inspection buffers."""
    global _ACTIVE_RAG_CHUNKS, _ACTIVE_AST_SYMBOLS
    _ACTIVE_RAG_CHUNKS.clear()
    _ACTIVE_AST_SYMBOLS.clear()


def register_ast_symbols(symbol_data: dict[str, Any]) -> None:
    """Register extracted AST symbols into active memory."""
    global _ACTIVE_AST_SYMBOLS
    file_path = symbol_data.get("file", "unknown")

    # Transform classes into structured memory chunks
    for cls in symbol_data.get("classes", []):
        chunk_id = f"{file_path}#class:{cls.get('name')}"
        if not any(existing.get("chunk_id") == chunk_id for existing in _ACTIVE_AST_SYMBOLS):
            _ACTIVE_AST_SYMBOLS.append({
                "chunk_id": chunk_id,
                "type": "class",
                "name": cls.get("name"),
                "file": file_path,
                "line_start": cls.get("line_start"),
                "line_end": cls.get("line_end"),
                "bases": cls.get("bases", []),
                "docstring": cls.get("docstring"),
                "methods": cls.get("methods", []),
            })

    # Transform top-level functions into structured memory chunks
    for fn in symbol_data.get("functions", []):
        chunk_id = f"{file_path}#func:{fn.get('name')}"
        if not any(existing.get("chunk_id") == chunk_id for existing in _ACTIVE_AST_SYMBOLS):
            _ACTIVE_AST_SYMBOLS.append({
                "chunk_id": chunk_id,
                "type": "function",
                "name": fn.get("name"),
                "signature": fn.get("signature"),
                "file": file_path,
                "line_start": fn.get("line_start"),
                "line_end": fn.get("line_end"),
                "is_async": fn.get("is_async", False),
                "docstring": fn.get("docstring"),
            })


def get_active_rag_chunks() -> list[dict[str, Any]]:
    """Return currently loaded RAG chunks."""
    return list(_ACTIVE_RAG_CHUNKS)


def get_active_ast_symbols() -> list[dict[str, Any]]:
    """Return currently loaded AST code symbols."""
    return list(_ACTIVE_AST_SYMBOLS)


# Backward-compatible alias for clearing active inspection memory
clear_memory_chunks = clear_memory_inspector


async def inspect_memory(
    source: str | None = None,
    code_path: str | None = None,
    chunk_size: int = 600,
    chunk_overlap: int = 100,
) -> dict[str, Any]:
    """Inspect or on-demand load and chunk RAG documents and AST symbols.

    Args:
        source: Optional document or directory path to load and chunk for RAG.
        code_path: Optional code file or directory path to inspect via AST.
        chunk_size: Max characters per chunk (default 600).
        chunk_overlap: Characters overlap between chunks (default 100).

    Returns:
        Structured dictionary containing loaded RAG chunks, AST symbol chunks, and summary.
    """
    # 1. On-demand RAG document loading
    if source:
        src_path = Path(source)
        if not src_path.is_absolute():
            src_path = Path.cwd() / src_path

        if src_path.exists():
            if src_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                register_rag_chunks([{
                    "id": f"img_{src_path.stem}",
                    "text": f"[Attached Image: {src_path.name}] (Provided directly to Vision model)",
                    "metadata": {"filename": src_path.name, "type": "image", "source": str(src_path)},
                }])
            else:
                try:
                    doc_mod = importlib.import_module("plugins.rag_chroma.document_loader")
                    if src_path.is_dir():
                        pages = doc_mod.load_directory(src_path)
                    else:
                        pages = doc_mod.load_document(src_path)

                    chunks = doc_mod.chunk_documents(pages, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
                    register_rag_chunks(chunks)
                except Exception as exc:
                    logger.warning("Failed to load document source '%s': %s", source, exc)

    # 2. On-demand AST Code Intelligence loading
    if code_path:
        try:
            intel_mod = importlib.import_module("plugins.tools_builtin.handlers.code_intel")
            ast_result = await intel_mod.get_symbol_map(path=code_path)
            if ast_result.get("ok"):
                data = ast_result.get("data", {})
                if "symbols" in data:
                    for file_sym in data.get("symbols", []):
                        register_ast_symbols(file_sym)
                else:
                    register_ast_symbols(data)
        except Exception as exc:
            logger.warning("Failed to inspect AST code path '%s': %s", code_path, exc)

    # 3. Default fallback if memory is still empty
    if not _ACTIVE_RAG_CHUNKS and not source:
        # Check if project README.md or AGENTS.md exists to provide initial context
        for candidate in ["README.md", "AGENTS.md", "docs/progress.md"]:
            cand_path = Path.cwd() / candidate
            if cand_path.is_file():
                try:
                    doc_mod = importlib.import_module("plugins.rag_chroma.document_loader")
                    pages = doc_mod.load_document(cand_path)
                    chunks = doc_mod.chunk_documents(pages[:2], chunk_size=chunk_size, chunk_overlap=chunk_overlap)
                    register_rag_chunks(chunks[:5])  # small sample
                    break
                except Exception:
                    pass

    if not _ACTIVE_AST_SYMBOLS and not code_path:
        # Default sample AST from core_logic/orchestrator.py
        orch_path = Path.cwd() / "core_logic" / "orchestrator.py"
        if orch_path.is_file():
            try:
                intel_mod = importlib.import_module("plugins.tools_builtin.handlers.code_intel")
                res = await intel_mod.get_symbol_map(path=str(orch_path))
                if res.get("ok"):
                    register_ast_symbols(res.get("data", {}))
            except Exception:
                pass

    # Extract distinct source filenames
    rag_sources = sorted(list({c.get("metadata", {}).get("filename", "unknown") for c in _ACTIVE_RAG_CHUNKS}))
    ast_sources = sorted(list({s.get("file", "unknown") for s in _ACTIVE_AST_SYMBOLS}))

    return {
        "ok": True,
        "summary": {
            "total_rag_chunks": len(_ACTIVE_RAG_CHUNKS),
            "total_ast_symbols": len(_ACTIVE_AST_SYMBOLS),
            "rag_sources": rag_sources,
            "ast_sources": ast_sources,
        },
        "rag": {
            "count": len(_ACTIVE_RAG_CHUNKS),
            "loaded_chunks": _ACTIVE_RAG_CHUNKS,
        },
        "ast": {
            "count": len(_ACTIVE_AST_SYMBOLS),
            "symbol_chunks": _ACTIVE_AST_SYMBOLS,
        },
    }
