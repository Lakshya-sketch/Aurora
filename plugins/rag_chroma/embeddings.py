"""Local Vector Embeddings Engine — Phase 4.2 Vector RAG.

Provides deterministic and neural vector embeddings:
1. `OllamaEmbeddings`: Connects to local Ollama / llama-server via `/api/embed` or `/api/embeddings`.
   - Supports batch embeddings
   - In-memory SHA256 caching of text embeddings
2. `HashingEmbeddings`: Pure Python deterministic term-frequency hashing vectorizer (384 dims).
   - Zero external dependencies
   - Works 100% offline even if Ollama server was not started with `--embeddings`
3. `UnifiedEmbeddings`: Auto-detects Ollama embedding capability; gracefully falls back
   to HashingEmbeddings if the local server returns 501 / is offline.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

import httpx

logger = logging.getLogger("aurora.rag.embeddings")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "qwen3.5:4b-q4_K_M"
DEFAULT_VECTOR_DIM = 384


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Calculate cosine similarity between two float vectors.

    Returns a score between -1.0 and 1.0 (typically 0.0 to 1.0 for normalized embeddings).
    """
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))

    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0

    return dot / (norm1 * norm2)


# ----------------------------------------------------------------------
# 1. Deterministic Fallback Vectorizer (Zero Dependencies)
# ----------------------------------------------------------------------

class HashingEmbeddings:
    """Deterministic hashing vectorizer using term frequency and L2 normalization.

    Generates 384-dimensional unit vectors using standard library hashlib + math.
    Guarantees semantic token overlap similarity without requiring GPU or external models.
    """

    def __init__(self, dimension: int = DEFAULT_VECTOR_DIM) -> None:
        self.dimension = dimension

    def _hash_token(self, token: str) -> int:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        return h % self.dimension

    def embed_query(self, text: str) -> list[float]:
        """Generate a 1D embedding vector for a single query string."""
        return self._embed_single(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate 1D embedding vectors for a list of document strings."""
        return [self._embed_single(t) for t in texts]

    def _embed_single(self, text: str) -> list[float]:
        if not text or not text.strip():
            return [0.0] * self.dimension

        words = re.findall(r"\b\w+\b", text.lower())
        if not words:
            return [0.0] * self.dimension

        vec = [0.0] * self.dimension

        # Unigrams
        for w in words:
            idx = self._hash_token(w)
            vec[idx] += 1.0

        # Bigrams for local phrase semantics
        for i in range(len(words) - 1):
            bigram = f"{words[i]}_{words[i+1]}"
            idx = self._hash_token(bigram)
            vec[idx] += 1.5

        # L2 unit normalization
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            return [round(x / norm, 6) for x in vec]
        return vec


# ----------------------------------------------------------------------
# 2. Local Ollama Neural Embeddings
# ----------------------------------------------------------------------

class OllamaEmbeddings:
    """Vector embeddings using local Ollama `/api/embed` or `/api/embeddings`."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_EMBED_MODEL,
        timeout_s: float = 30.0,
        batch_size: int = 16,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.batch_size = batch_size
        self._cache: dict[str, list[float]] = {}
        self._server_supports_embed: bool | None = None

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}:{text}".encode("utf-8")).hexdigest()

    async def is_available(self) -> bool:
        """Check if local server is reachable and supports embeddings."""
        if self._server_supports_embed is not None:
            return self._server_supports_embed

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(4.0)) as client:
                res = await client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": "test"},
                )
                if res.status_code == 200:
                    self._server_supports_embed = True
                    return True
                else:
                    logger.debug("Ollama /api/embed returned status %d: %s", res.status_code, res.text)
                    self._server_supports_embed = False
                    return False
        except Exception as exc:
            logger.debug("Ollama is_available check failed: %s", exc)
            self._server_supports_embed = False
            return False

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        results = await self.embed_documents([text])
        return results[0] if results else [0.0] * DEFAULT_VECTOR_DIM

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text chunks with batching and memory caching."""
        if not texts:
            return []

        embeddings: list[list[float] | None] = [None] * len(texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        # Check cache
        for idx, t in enumerate(texts):
            key = self._cache_key(t)
            if key in self._cache:
                embeddings[idx] = self._cache[key]
            else:
                missing_indices.append(idx)
                missing_texts.append(t)

        if not missing_texts:
            return [e for e in embeddings if e is not None]

        # Call Ollama API in batches
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s)) as client:
            for i in range(0, len(missing_texts), self.batch_size):
                batch_texts = missing_texts[i : i + self.batch_size]
                batch_indices = missing_indices[i : i + self.batch_size]

                try:
                    res = await client.post(
                        f"{self.base_url}/api/embed",
                        json={"model": self.model, "input": batch_texts},
                    )

                    if res.status_code == 200:
                        data = res.json()
                        batch_embs = data.get("embeddings", [])
                        for b_idx, emb in zip(batch_indices, batch_embs):
                            embeddings[b_idx] = emb
                            self._cache[self._cache_key(texts[b_idx])] = emb
                    elif res.status_code == 501:
                        # Server does not support embeddings (e.g. started without --embeddings)
                        raise NotImplementedError(
                            "Ollama / llama-server does not support embeddings. Start it with '--embeddings'."
                        )
                    else:
                        raise RuntimeError(f"Ollama embed returned {res.status_code}: {res.text}")

                except (httpx.RequestError, NotImplementedError, RuntimeError) as exc:
                    logger.warning("Ollama embed batch failed (%s), fallback may be required", exc)
                    raise exc

        return [e for e in embeddings if e is not None]


# ----------------------------------------------------------------------
# 3. Unified Embeddings Provider (Auto-Fallback)
# ----------------------------------------------------------------------

class UnifiedEmbeddings:
    """Robust unified embeddings manager.

    Tries Ollama neural embeddings first. If the local server is offline or was
    started without `--embeddings`, it gracefully falls back to deterministic
    `HashingEmbeddings` to ensure zero-downtime operation.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_EMBED_MODEL,
        dimension: int = DEFAULT_VECTOR_DIM,
    ) -> None:
        self.ollama = OllamaEmbeddings(base_url=base_url, model=model)
        self.fallback = HashingEmbeddings(dimension=dimension)
        self._using_fallback = False

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        if not self._using_fallback:
            try:
                if await self.ollama.is_available():
                    return await self.ollama.embed_query(text)
                else:
                    self._using_fallback = True
            except Exception:
                self._using_fallback = True

        return self.fallback.embed_query(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text chunks."""
        if not self._using_fallback:
            try:
                if await self.ollama.is_available():
                    return await self.ollama.embed_documents(texts)
                else:
                    self._using_fallback = True
            except Exception:
                self._using_fallback = True

        return self.fallback.embed_documents(texts)

    @property
    def is_using_fallback(self) -> bool:
        """Return True if currently operating in fallback hashing vector mode."""
        return self._using_fallback
