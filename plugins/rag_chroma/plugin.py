"""RAG Chroma Plugin — Phase 4.2: Vector Ingestion & Semantic Retrieval.

Provides local document vector indexing and semantic similarity search.
Subscribes to:
- ``rag.index``: Ingest and index documents/directories into the vector store.
- ``rag.search``: Retrieve top-K semantically relevant document chunks for a query.
- ``tool.execute``: Handles ``rag_search`` tool calls from LLM function calling loop.
- ``tools.query`` & ``kernel.ready``: Broadcasts ``rag_search`` tool schema on ``tools.catalog``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aurora_core.plugin_base import BasePlugin, PluginContext
from plugins.rag_chroma.document_loader import (
    DocumentChunk,
    chunk_documents,
    load_directory,
    load_document,
)
from plugins.rag_chroma.embeddings import UnifiedEmbeddings
from plugins.rag_chroma.store import VectorStore

logger = logging.getLogger("aurora.plugin.rag_chroma")


class RagChromaPlugin(BasePlugin):
    """Vector RAG plugin providing semantic document ingestion and retrieval."""

    name = "rag_chroma"
    version = "0.1.0"
    capabilities = ["rag.search", "rag.index", "tools.catalog"]
    requires: list[str] = []
    subscribes = ["rag.index", "rag.search", "tool.execute", "tools.query", "kernel.ready", "database.clear", "rag.clear"]

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self.embeddings: UnifiedEmbeddings | None = None
        self.store: VectorStore | None = None

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return the tool schema for semantic document search."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "rag_search",
                    "description": (
                        "Search local indexed documents, uploaded PDFs, notes, and manuals "
                        "using semantic vector similarity to retrieve relevant factual context."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query or factual topic to look up in documents.",
                            },
                            "k": {
                                "type": "integer",
                                "description": "Maximum number of relevant chunks to retrieve (default: 5).",
                            },
                        },
                        "required": ["query"],
                    },
                },
            }
        ]

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        candidates: list[Path] = []
        chroma_dir_str = ctx.config.get("rag", {}).get("chroma_dir")
        if chroma_dir_str:
            candidates.append(Path(chroma_dir_str))
        candidates.append(Path("chroma"))
        data_dir_str = ctx.config.get("aurora", {}).get("data_dir", "data")
        candidates.append(Path(data_dir_str) / "chroma")

        # Pick candidate with existing vector_store.json records if available
        persist_dir = candidates[0]
        for c in candidates:
            json_file = c / "vector_store.json"
            if json_file.exists() and json_file.stat().st_size > 50:
                persist_dir = c
                break

        self.embeddings = UnifiedEmbeddings()
        self.store = VectorStore(persist_dir=persist_dir)

        ctx.logger.info(
            "rag_chroma ready: persist_dir=%s, indexed_chunks=%d",
            persist_dir,
            self.store.count(),
        )

        # Broadcast RAG tool schema to LLM plugins via EventBus
        await ctx.bus.publish("tools.catalog", {"schemas": self.get_tool_schemas()})

    async def shutdown(self) -> None:
        if self._ctx:
            self._ctx.logger.info("rag_chroma shutting down")
        if self.store:
            self.store.save()
        self._ctx = None

    async def _index_paths(
        self,
        paths: list[str | Path],
        session_id: str = "default",
        chunk_size: int = 600,
        chunk_overlap: int = 100,
    ) -> tuple[int, list[DocumentPage], list[DocumentChunk]]:
        """Load, chunk, embed, and store documents from given paths."""
        assert self.store is not None and self.embeddings is not None
        all_pages: list[DocumentPage] = []
        all_chunks: list[DocumentChunk] = []

        for p_raw in paths:
            p = Path(p_raw)
            if not p.exists():
                logger.warning("rag.index skipped non-existent path: %s", p)
                continue

            try:
                if p.is_dir():
                    pages = load_directory(p)
                else:
                    pages = load_document(p)

                all_pages.extend(pages)
                chunks = chunk_documents(pages, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
                for c in chunks:
                    c.metadata["session_id"] = session_id
                all_chunks.extend(chunks)
            except Exception as exc:
                logger.warning("rag.index failed to load %s: %s", p, exc)

        if not all_chunks:
            return 0, all_pages, []

        # Generate vector embeddings
        texts = [c.text for c in all_chunks]
        embeddings = await self.embeddings.embed_documents(texts)

        # Store in vector index
        count = self.store.add(all_chunks, embeddings)
        return count, all_pages, all_chunks

    async def handle_event(self, topic: str, payload: dict[str, Any]) -> None:
        assert self._ctx is not None
        assert self.store is not None and self.embeddings is not None

        # 1. Advertise tool schemas
        if topic in ("tools.query", "kernel.ready"):
            await self._ctx.bus.publish("tools.catalog", {"schemas": self.get_tool_schemas()})
            return

        # 2. Ingest & Index Documents
        if topic == "rag.index":
            paths = payload.get("file_paths") or payload.get("files") or []
            chunk_size = payload.get("chunk_size", 600)
            chunk_overlap = payload.get("chunk_overlap", 100)
            call_id = payload.get("call_id", "")
            session_id = payload.get("session_id", "default")

            indexed_count, pages, chunks = await self._index_paths(
                paths,
                session_id=session_id,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

            await self._ctx.bus.publish(
                "rag.indexed",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "file_paths": [str(p) for p in paths],
                    "chunks_count": indexed_count,
                    "pages": [
                        p.to_dict() if hasattr(p, "to_dict") else {"text": getattr(p, "text", str(p)), "page_number": getattr(p, "page_number", 1)}
                        for p in pages
                    ],
                    "chunks": [
                        c.to_dict() if hasattr(c, "to_dict") else {"chunk_id": getattr(c, "chunk_id", ""), "text": getattr(c, "text", str(c)), "metadata": getattr(c, "metadata", {})}
                        for c in chunks
                    ],
                    "total_store_count": self.store.count(),
                    "ok": True,
                },
            )
            return

        # 3. Semantic Vector Search
        if topic == "rag.search":
            query = payload.get("query", "")
            k = payload.get("k", 5)
            call_id = payload.get("call_id", "")
            session_id = payload.get("session_id", "default")
            filter_meta = payload.get("filter")
            threshold = payload.get("score_threshold", 0.0)

            if not query:
                results = []
            else:
                query_emb = await self.embeddings.embed_query(query)
                results = self.store.similarity_search(
                    query_emb,
                    k=k,
                    score_threshold=threshold,
                    filter=filter_meta,
                    session_id=session_id,
                )

            await self._ctx.bus.publish(
                "rag.result",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "query": query,
                    "results": results,
                    "count": len(results),
                    "ok": True,
                },
            )
            return

        # 4. LLM Function Calling Tool Execution (rag_search)
        if topic == "tool.execute":
            tool_name = payload.get("tool", "")
            if tool_name != "rag_search":
                return

            call_id = payload.get("call_id", "")
            session_id = payload.get("session_id", "default")
            args = payload.get("args", {})
            query = args.get("query", "")
            k = args.get("k", 5)

            if not query:
                results = []
            else:
                query_emb = await self.embeddings.embed_query(query)
                results = self.store.similarity_search(query_emb, k=k)

            await self._ctx.bus.publish(
                "tool.result",
                {
                    "call_id": call_id,
                    "session_id": session_id,
                    "tool": "rag_search",
                    "ok": True,
                    "data": {
                        "results": results,
                        "count": len(results),
                    },
                    "error": None,
                },
            )
            return

        # 5. Clear vector store
        if topic in ("database.clear", "rag.clear"):
            if self.store:
                self.store.clear()
            logger.info("rag_chroma: vector store cleared to 0")
            return


PLUGIN = RagChromaPlugin
