"""Vector Store & Semantic Cosine Index — Phase 4.2 Vector RAG.

Provides persistent vector indexing and cosine similarity retrieval for document chunks.
Supports:
1. Pure Python deterministic vector indexing (works 100% offline with zero extra dependencies)
2. Cosine similarity search with score thresholding and metadata filtering
3. Atomic JSON persistence to `data/chroma/vector_store.json`
4. Seamless compatibility with Chroma / LangChain retriever contracts
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from plugins.rag_chroma.embeddings import cosine_similarity

logger = logging.getLogger("aurora.rag.store")


@dataclass
class VectorRecord:
    """Represents a single embedded chunk in the vector index."""
    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "metadata": self.metadata,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VectorRecord:
        return cls(
            chunk_id=data.get("chunk_id", ""),
            text=data.get("text", ""),
            metadata=data.get("metadata", {}),
            embedding=data.get("embedding", []),
        )


class VectorStore:
    """Persistent vector store with cosine similarity retrieval."""

    def __init__(self, persist_dir: Path | str | None = None) -> None:
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self._records: dict[str, VectorRecord] = {}

        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self.load()

    @property
    def store_file(self) -> Path | None:
        if not self.persist_dir:
            return None
        return self.persist_dir / "vector_store.json"

    def count(self) -> int:
        """Return total number of indexed vector records."""
        return len(self._records)

    def add(
        self,
        chunks: list[Any],
        embeddings: list[list[float]],
    ) -> int:
        """Add document chunks and their embedding vectors to the index.

        Args:
            chunks: List of `DocumentChunk` or dicts containing chunk_id, text, metadata.
            embeddings: Parallel list of float embedding vectors.

        Returns:
            Number of successfully added/updated records.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunks count ({len(chunks)}) does not match embeddings count ({len(embeddings)})"
            )

        added_count = 0
        for chunk, emb in zip(chunks, embeddings):
            if hasattr(chunk, "chunk_id"):
                c_id = chunk.chunk_id
                c_text = chunk.text
                c_meta = chunk.metadata
            elif isinstance(chunk, dict):
                c_id = chunk.get("chunk_id", "")
                c_text = chunk.get("text", "")
                c_meta = chunk.get("metadata", {})
            else:
                continue

            record = VectorRecord(
                chunk_id=c_id,
                text=c_text,
                metadata=dict(c_meta),
                embedding=list(emb),
            )
            self._records[c_id] = record
            added_count += 1

        if added_count > 0 and self.persist_dir:
            self.save()

        return added_count

    def similarity_search(
        self,
        query_embedding: list[float],
        k: int = 5,
        score_threshold: float = 0.0,
        filter: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find the top-K most similar document chunks to the query vector.

        Args:
            query_embedding: 1D query vector.
            k: Max number of top records to return.
            score_threshold: Minimum cosine similarity required (0.0 to 1.0).
            filter: Optional metadata key-value filters.
            session_id: Optional session identifier to scope retrieval to this chat.

        Returns:
            List of matching records with similarity score, sorted descending.
        """
        if not self._records or not query_embedding:
            return []

        candidates: list[tuple[float, VectorRecord]] = []

        for record in self._records.values():
            if not record.embedding:
                continue

            # Apply metadata filter if provided
            if filter:
                match = True
                for f_key, f_val in filter.items():
                    if record.metadata.get(f_key) != f_val:
                        match = False
                        break
                if not match:
                    continue

            # Apply session scope if session_id is provided
            if session_id and session_id not in ("global", "*"):
                rec_sess = record.metadata.get("session_id")
                if rec_sess is not None and rec_sess not in (session_id, "global", "*"):
                    continue
                if rec_sess is None and session_id not in ("default", "orchestrator"):
                    continue

            sim = cosine_similarity(query_embedding, record.embedding)
            if sim >= score_threshold:
                candidates.append((sim, record))

        # Sort descending by cosine similarity score
        candidates.sort(key=lambda item: item[0], reverse=True)

        results: list[dict[str, Any]] = []
        for score, rec in candidates[:k]:
            results.append({
                "chunk_id": rec.chunk_id,
                "text": rec.text,
                "metadata": rec.metadata,
                "score": round(score, 4),
            })

        return results

    def get(self, chunk_id: str) -> dict[str, Any] | None:
        """Retrieve a single record by chunk_id."""
        rec = self._records.get(chunk_id)
        if rec:
            return rec.to_dict()
        return None

    def delete(self, chunk_ids: list[str]) -> int:
        """Delete records by chunk IDs."""
        deleted = 0
        for c_id in chunk_ids:
            if c_id in self._records:
                del self._records[c_id]
                deleted += 1

        if deleted > 0 and self.persist_dir:
            self.save()

        return deleted

    def clear(self) -> None:
        """Clear all records from memory and disk."""
        self._records.clear()
        if self.persist_dir and self.persist_dir.exists():
            for f in self.persist_dir.glob("vector_store.*"):
                try:
                    f.unlink()
                except Exception as exc:
                    logger.warning("Failed to remove file %s: %s", f, exc)
        elif self.store_file and self.store_file.exists():
            try:
                self.store_file.unlink()
            except Exception as exc:
                logger.warning("Failed to remove vector store file: %s", exc)

    def save(self) -> None:
        """Save vector records to disk atomically."""
        if not self.store_file:
            return

        tmp_path = self.store_file.with_suffix(".tmp")
        try:
            payload = {
                "version": "1.0",
                "count": len(self._records),
                "records": [r.to_dict() for r in self._records.values()],
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_path, self.store_file)
            logger.debug("Saved %d vector records to %s", len(self._records), self.store_file)
        except Exception as exc:
            logger.error("Failed to save vector store: %s", exc)
            if tmp_path.exists():
                tmp_path.unlink()

    def load(self) -> None:
        """Load vector records from disk."""
        if not self.store_file or not self.store_file.is_file():
            return

        try:
            with open(self.store_file, "r", encoding="utf-8") as f:
                payload = json.load(f)

            records_data = payload.get("records", [])
            loaded: dict[str, VectorRecord] = {}
            for d in records_data:
                rec = VectorRecord.from_dict(d)
                if rec.chunk_id:
                    loaded[rec.chunk_id] = rec

            self._records = loaded
            logger.info("Loaded %d vector records from %s", len(self._records), self.store_file)
        except Exception as exc:
            logger.warning("Failed to load vector store from %s: %s", self.store_file, exc)
