"""Unit tests for Vector Embeddings (Phase 4.2 Vector RAG)."""

from __future__ import annotations

import math
import pytest

from plugins.rag_chroma.embeddings import (
    cosine_similarity,
    HashingEmbeddings,
    OllamaEmbeddings,
    UnifiedEmbeddings,
)


# ----------------------------------------------------------------------
# 1. Cosine Similarity Tests
# ----------------------------------------------------------------------

def test_cosine_similarity_identical():
    v = [1.0, 2.0, 3.0]
    sim = cosine_similarity(v, v)
    assert pytest.approx(sim, 0.0001) == 1.0


def test_cosine_similarity_orthogonal():
    v1 = [1.0, 0.0]
    v2 = [0.0, 1.0]
    assert cosine_similarity(v1, v2) == 0.0


def test_cosine_similarity_mismatched_length():
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0


def test_cosine_similarity_zero_norm():
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ----------------------------------------------------------------------
# 2. HashingEmbeddings Tests
# ----------------------------------------------------------------------

def test_hashing_embeddings_dimension_and_norm():
    embedder = HashingEmbeddings(dimension=384)
    vec = embedder.embed_query("Aurora microkernel architecture")

    assert len(vec) == 384
    # Check L2 unit normalization: sum of squares == 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    assert pytest.approx(norm, 0.001) == 1.0


def test_hashing_embeddings_semantic_similarity():
    embedder = HashingEmbeddings(dimension=384)
    v_a = embedder.embed_query("Aurora uses an event bus microkernel architecture.")
    v_b = embedder.embed_query("The microkernel architecture in Aurora relies on an event bus.")
    v_c = embedder.embed_query("How to bake a chocolate cake in the oven.")

    sim_ab = cosine_similarity(v_a, v_b)
    sim_ac = cosine_similarity(v_a, v_c)

    # Semantic overlap between A & B should be significantly higher than A & C
    assert sim_ab > 0.45
    assert sim_ac < 0.15
    assert sim_ab > sim_ac * 3


def test_hashing_embeddings_empty_text():
    embedder = HashingEmbeddings(dimension=384)
    vec = embedder.embed_query("")
    assert vec == [0.0] * 384


def test_hashing_embeddings_documents_batch():
    embedder = HashingEmbeddings(dimension=128)
    docs = ["Doc 1 text", "Doc 2 text", "Doc 3 text"]
    vecs = embedder.embed_documents(docs)
    assert len(vecs) == 3
    assert all(len(v) == 128 for v in vecs)


# ----------------------------------------------------------------------
# 3. UnifiedEmbeddings Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unified_embeddings_fallback():
    """Verify that UnifiedEmbeddings falls back to HashingEmbeddings when server returns 501 or offline."""
    unified = UnifiedEmbeddings(base_url="http://127.0.0.1:59999", dimension=384)
    query_vec = await unified.embed_query("test query")

    assert len(query_vec) == 384
    assert unified.is_using_fallback is True


@pytest.mark.asyncio
async def test_unified_embeddings_documents():
    unified = UnifiedEmbeddings(base_url="http://127.0.0.1:59999", dimension=384)
    doc_vecs = await unified.embed_documents(["First document", "Second document"])

    assert len(doc_vecs) == 2
    assert all(len(v) == 384 for v in doc_vecs)
