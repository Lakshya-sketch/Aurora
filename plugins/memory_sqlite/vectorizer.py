"""Deterministic Hashing Vectorizer & Cosine Similarity for SQLite Memory.

Provides self-contained, zero-dependency 384-dimensional unit vector embeddings
and cosine similarity comparison using standard library `hashlib` and `math`.
Ensures Rule 2 compliance by avoiding cross-plugin imports.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

DEFAULT_VECTOR_DIM = 384


def cosine_similarity(vec1: Sequence[float], vec2: Sequence[float]) -> float:
    """Calculate cosine similarity between two float vectors (-1.0 to 1.0)."""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))

    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0

    return dot / (norm1 * norm2)


class MemoryVectorizer:
    """Pure-Python deterministic term-frequency vectorizer with L2 normalization."""

    def __init__(self, dimension: int = DEFAULT_VECTOR_DIM) -> None:
        self.dimension = dimension

    def _hash_token(self, token: str) -> int:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        return h % self.dimension

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string into a 384-dimensional normalized vector."""
        if not text or not text.strip():
            return [0.0] * self.dimension

        words = re.findall(r"\b\w+\b", text.lower())
        if not words:
            return [0.0] * self.dimension

        vec = [0.0] * self.dimension

        # Unigram features
        for w in words:
            idx = self._hash_token(w)
            vec[idx] += 1.0

        # Bigram features for local semantic phrasing
        for i in range(len(words) - 1):
            bigram = f"{words[i]}_{words[i+1]}"
            idx = self._hash_token(bigram)
            vec[idx] += 1.5

        # L2 unit normalization
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0.0:
            return [round(x / norm, 6) for x in vec]
        return vec
