"""Tests for semantic/vector memory: EmbeddingStore, SemanticRetriever, LLM embed()."""

from __future__ import annotations

import struct
import time
from pathlib import Path
from unittest.mock import MagicMock

# ── helpers ───────────────────────────────────────────────────────────────────

def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _make_llm(vectors: list[list[float]] | None = None):
    llm = MagicMock()
    if vectors is not None:
        llm.embed.return_value = vectors
    else:
        llm.embed.side_effect = Exception("no embeddings")
    return llm


def _make_store(tmp_path: Path, llm=None):
    from macroa.memory.semantic import EmbeddingStore
    return EmbeddingStore(db_path=tmp_path / "embeddings.db", llm=llm)


# ── cosine similarity ─────────────────────────────────────────────────────────

class TestCosine:
    def test_identical_vectors_score_1(self):
        from macroa.memory.semantic import _cosine
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors_score_0(self):
        from macroa.memory.semantic import _cosine
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine(a, b)) < 1e-6

    def test_opposite_vectors_score_minus_1(self):
        from macroa.memory.semantic import _cosine
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine(a, b) - (-1.0)) < 1e-6

    def test_zero_vector_returns_0(self):
        from macroa.memory.semantic import _cosine
        assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


# ── pack/unpack roundtrip ─────────────────────────────────────────────────────

class TestPackUnpack:
    def test_roundtrip_preserves_values(self):
        from macroa.memory.semantic import _pack, _unpack
        v = [0.1, -0.5, 0.9, 0.0]
        blob = _pack(v)
        result = _unpack(blob)
        assert len(result) == len(v)
        for a, b in zip(v, result):
            assert abs(a - b) < 1e-6


# ── EmbeddingStore ────────────────────────────────────────────────────────────

class TestEmbeddingStore:
    def test_count_empty(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert store.count() == 0

    def test_queue_embed_and_flush(self, tmp_path: Path):
        vec = [0.1] * 4
        llm = _make_llm(vectors=[vec])
        store = _make_store(tmp_path, llm)
        # Directly populate _pending without spawning thread (avoids race)
        store._pending.append(("user", "name", "Alice"))
        store._flush_pending()
        assert store.count() == 1

    def test_search_returns_similar(self, tmp_path: Path):
        vec_a = [1.0, 0.0, 0.0]
        vec_q = [1.0, 0.0, 0.0]   # identical → score = 1.0
        vec_b = [0.0, 1.0, 0.0]   # orthogonal → score = 0.0

        llm = MagicMock()
        store = _make_store(tmp_path, llm)

        # Manually insert two embeddings
        import sqlite3

        from macroa.memory.semantic import _pack
        with sqlite3.connect(str(tmp_path / "embeddings.db")) as conn:
            conn.execute("""
                INSERT INTO embeddings (id, namespace, key, model, vector, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("user/name", "user", "name", "test", _pack(vec_a), time.time()))
            conn.execute("""
                INSERT INTO embeddings (id, namespace, key, model, vector, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("user/other", "user", "other", "test", _pack(vec_b), time.time()))

        # Embed query
        llm.embed.return_value = [vec_q]
        results = store.search("name query", min_score=0.5)
        assert len(results) == 1
        assert results[0][1] == "name"
        assert results[0][2] > 0.99

    def test_search_no_llm_returns_empty(self, tmp_path: Path):
        store = _make_store(tmp_path, llm=None)
        assert store.search("anything") == []

    def test_search_llm_failure_returns_empty(self, tmp_path: Path):
        llm = _make_llm(vectors=None)
        store = _make_store(tmp_path, llm)
        results = store.search("query")
        assert results == []

    def test_delete_removes_embedding(self, tmp_path: Path):
        import sqlite3

        from macroa.memory.semantic import _pack
        store = _make_store(tmp_path)
        with sqlite3.connect(str(tmp_path / "embeddings.db")) as conn:
            conn.execute("""
                INSERT INTO embeddings (id, namespace, key, model, vector, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("u/k", "u", "k", "test", _pack([1.0, 0.0]), time.time()))
        assert store.count() == 1
        store.delete("u", "k")
        assert store.count() == 0

    def test_query_embedding_cached(self, tmp_path: Path):
        llm = _make_llm(vectors=[[1.0, 0.0]])
        store = _make_store(tmp_path, llm)
        store._embed_query("test")
        store._embed_query("test")
        # Second call should use cache — embed only called once
        assert llm.embed.call_count == 1

    def test_set_llm_after_construction(self, tmp_path: Path):
        store = _make_store(tmp_path, llm=None)
        assert store._llm is None
        llm = _make_llm(vectors=[[1.0]])
        store.set_llm(llm)
        assert store._llm is llm


# ── SemanticRetriever ─────────────────────────────────────────────────────────

class TestSemanticRetriever:
    def _make_memory(self):
        memory = MagicMock()
        memory.list_pinned.return_value = []
        memory.search_fts.return_value = [
            {"namespace": "user", "key": "name", "value": "Alice",
             "confidence": 1.0, "source": "user", "updated_at": 0.0, "pinned": False}
        ]
        memory.get.return_value = "Bob"
        return memory

    def test_no_embedding_store_uses_fts_only(self):
        from macroa.memory.semantic import SemanticRetriever
        memory = self._make_memory()
        retriever = SemanticRetriever(memory=memory, embedding_store=None)
        results = retriever.retrieve("what is my name?")
        assert any(r["key"] == "name" for r in results)

    def test_semantic_results_merged_with_fts(self, tmp_path: Path):
        import sqlite3

        from macroa.memory.semantic import EmbeddingStore, SemanticRetriever, _pack

        # Set up embedding store with one entry NOT in FTS5 results
        llm = _make_llm(vectors=[[1.0, 0.0]])
        store = EmbeddingStore(db_path=tmp_path / "emb.db", llm=llm)
        with sqlite3.connect(str(tmp_path / "emb.db")) as conn:
            conn.execute("""
                INSERT INTO embeddings (id, namespace, key, model, vector, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("user/city", "user", "city", "test", _pack([1.0, 0.0]), time.time()))

        memory = self._make_memory()
        memory.search_fts.return_value = []  # FTS finds nothing
        memory.get.return_value = "Paris"

        retriever = SemanticRetriever(memory=memory, embedding_store=store)
        results = retriever.retrieve("where do I live?")
        # Semantic result "city" should appear
        assert any(r["key"] == "city" for r in results)

    def test_duplicate_keys_not_doubled(self, tmp_path: Path):
        import sqlite3

        from macroa.memory.semantic import EmbeddingStore, SemanticRetriever, _pack

        # Both FTS5 and semantic find "name"
        llm = _make_llm(vectors=[[1.0, 0.0]])
        store = EmbeddingStore(db_path=tmp_path / "emb.db", llm=llm)
        with sqlite3.connect(str(tmp_path / "emb.db")) as conn:
            conn.execute("""
                INSERT INTO embeddings (id, namespace, key, model, vector, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("user/name", "user", "name", "test", _pack([1.0, 0.0]), time.time()))

        memory = self._make_memory()
        retriever = SemanticRetriever(memory=memory, embedding_store=store)
        results = retriever.retrieve("what is my name?")
        keys = [r["key"] for r in results]
        # "name" should appear at most once
        assert keys.count("name") <= 1


# ── LLMDriver.embed() ─────────────────────────────────────────────────────────

class TestLLMDriverEmbed:
    def test_embed_returns_vectors(self):
        from macroa.drivers.llm_driver import LLMDriver

        driver = LLMDriver.__new__(LLMDriver)
        mock_client = MagicMock()
        driver._client = mock_client

        mock_item = MagicMock()
        mock_item.index = 0
        mock_item.embedding = [0.1, 0.2, 0.3]
        mock_client.embeddings.create.return_value = MagicMock(data=[mock_item])

        result = driver.embed(["hello world"])
        assert result == [[0.1, 0.2, 0.3]]
        mock_client.embeddings.create.assert_called_once()

    def test_embed_empty_list_returns_empty(self):
        from macroa.drivers.llm_driver import LLMDriver

        driver = LLMDriver.__new__(LLMDriver)
        driver._client = MagicMock()
        result = driver.embed([])
        assert result == []
        driver._client.embeddings.create.assert_not_called()

    def test_embed_api_error_raises(self):
        from openai import APIError

        from macroa.drivers.llm_driver import LLMDriver, LLMDriverError

        driver = LLMDriver.__new__(LLMDriver)
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = APIError(
            message="rate limited", request=MagicMock(), body=None
        )
        driver._client = mock_client

        try:
            driver.embed(["text"])
            assert False, "Expected LLMDriverError"
        except LLMDriverError:
            pass

    def test_embed_preserves_order(self):
        from macroa.drivers.llm_driver import LLMDriver

        driver = LLMDriver.__new__(LLMDriver)
        mock_client = MagicMock()
        driver._client = mock_client

        # Return items in reverse order
        item0 = MagicMock(index=0, embedding=[1.0, 0.0])
        item1 = MagicMock(index=1, embedding=[0.0, 1.0])
        mock_client.embeddings.create.return_value = MagicMock(data=[item1, item0])

        result = driver.embed(["first", "second"])
        assert result[0] == [1.0, 0.0]  # index 0 = first
        assert result[1] == [0.0, 1.0]  # index 1 = second


# ── MemoryDriver embedding integration ────────────────────────────────────────

class TestMemoryDriverEmbeddingIntegration:
    def test_set_fact_queues_embedding(self, tmp_path: Path):
        from macroa.drivers.memory_driver import MemoryDriver

        mem = MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db")
        store = MagicMock()
        mem.set_embedding_store(store)
        mem.set("user", "color", "blue")

        store.queue_embed.assert_called_once_with("user", "color", "color: blue")

    def test_no_embedding_store_set_fact_still_works(self, tmp_path: Path):
        from macroa.drivers.memory_driver import MemoryDriver

        mem = MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db")
        mem.set("user", "key", "value")  # should not raise
        assert mem.get("user", "key") == "value"
