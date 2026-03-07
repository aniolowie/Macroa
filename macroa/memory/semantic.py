"""Semantic (vector) memory — embedding-based similarity search.

Augments the FTS5 keyword search with true semantic similarity:
  - On fact write: generate embedding + store in embeddings.db
  - On query: embed the query, compute cosine similarity, merge with FTS5 results

Architecture
------------
EmbeddingStore  — SQLite-backed {key, namespace, vector BLOB} table
                  Pure Python cosine similarity (no numpy required)
                  LRU cache for frequently queried embeddings

SemanticRetriever — Two-bucket merge:
  1. FTS5 keyword bucket  (from retriever.py)
  2. Semantic bucket       (EmbeddingStore cosine similarity)
  Deduplicates on key, ranks by combined score.

Graceful degradation:
  - If embedding API unavailable → fall back to FTS5 silently
  - If embeddings.db corrupted → fall back to FTS5 silently
  - Embedding generation is async (daemon thread) — writes never block reads

Storage:
  ~/.macroa/memory/embeddings.db
  Table: embeddings (id TEXT PK, namespace TEXT, key TEXT,
                     model TEXT, vector BLOB, created_at REAL)
  Vector: struct.pack('f' * dims, *floats) — float32, ~6KB per 1536-dim vector
"""

from __future__ import annotations

import logging
import sqlite3
import struct
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_EMBED_MODEL = "openai/text-embedding-3-small"
_CACHE_SIZE = 128  # in-memory LRU for query embeddings


# ── Pure-Python cosine similarity ─────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in [0, 1] (or 0 on zero vectors)."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{n}f", blob))


# ── EmbeddingStore ────────────────────────────────────────────────────────────

class EmbeddingStore:
    """SQLite-backed store for text embeddings.

    Thread-safe: uses WAL mode + per-operation connections.
    Embedding generation happens in a background thread (non-blocking for callers).
    """

    def __init__(self, db_path: Path, llm=None) -> None:
        """
        Args:
            db_path: Path to the embeddings SQLite database.
            llm:     LLMDriver instance (needed for embedding generation).
                     Can be set later via .set_llm(llm).
        """
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._llm = llm
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str, str]] = []   # (namespace, key, text)
        self._cache: dict[str, list[float]] = {}          # query_text → vector (LRU)
        self._init_db()

    def set_llm(self, llm) -> None:
        self._llm = llm

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id          TEXT NOT NULL,
                    namespace   TEXT NOT NULL,
                    key         TEXT NOT NULL,
                    model       TEXT NOT NULL,
                    vector      BLOB NOT NULL,
                    created_at  REAL NOT NULL,
                    PRIMARY KEY (namespace, key, model)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS emb_ns_key ON embeddings (namespace, key)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── Write (async) ─────────────────────────────────────────────────────────

    def queue_embed(self, namespace: str, key: str, text: str) -> None:
        """Queue a fact for background embedding. Returns immediately."""
        with self._lock:
            self._pending.append((namespace, key, text))
        # Flush in a daemon thread to not block the caller
        t = threading.Thread(target=self._flush_pending, daemon=True, name="macroa-embed")
        t.start()

    def _flush_pending(self) -> None:
        """Embed all pending facts in one batched API call."""
        with self._lock:
            if not self._pending:
                return
            batch = self._pending[:]
            self._pending.clear()

        if not self._llm:
            return

        texts = [text for _, _, text in batch]
        try:
            vectors = self._llm.embed(texts, model=_EMBED_MODEL)
        except Exception as exc:
            logger.debug("Embedding generation failed: %s", exc)
            return

        with self._connect() as conn:
            for (ns, key, _), vector in zip(batch, vectors):
                blob = _pack(vector)
                conn.execute("""
                    INSERT OR REPLACE INTO embeddings
                        (id, namespace, key, model, vector, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (f"{ns}/{key}", ns, key, _EMBED_MODEL, blob, time.time()))

        logger.debug("Embedded %d facts", len(batch))

    # ── Read ──────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 10,
        namespace: str | None = None,
        min_score: float = 0.5,
    ) -> list[tuple[str, str, float]]:
        """Search by semantic similarity.

        Returns list of (namespace, key, score) sorted by score descending.
        Returns [] if embeddings unavailable or query embedding fails.
        """
        query_vec = self._embed_query(query)
        if query_vec is None:
            return []

        # Fetch all stored vectors (filtered by namespace if given)
        try:
            with self._connect() as conn:
                if namespace:
                    rows = conn.execute(
                        "SELECT namespace, key, vector FROM embeddings WHERE namespace=?",
                        (namespace,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT namespace, key, vector FROM embeddings"
                    ).fetchall()
        except sqlite3.OperationalError:
            return []

        scored: list[tuple[str, str, float]] = []
        for ns, key, blob in rows:
            try:
                vec = _unpack(blob)
                score = _cosine(query_vec, vec)
                if score >= min_score:
                    scored.append((ns, key, score))
            except Exception:
                continue

        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:limit]

    def _embed_query(self, text: str) -> list[float] | None:
        """Embed a query string (with LRU caching). Returns None on failure."""
        # Check cache
        if text in self._cache:
            return self._cache[text]

        if not self._llm:
            return None
        try:
            vectors = self._llm.embed([text], model=_EMBED_MODEL)
            if not vectors:
                return None
            vec = vectors[0]
            # Simple LRU: evict oldest if cache is full
            if len(self._cache) >= _CACHE_SIZE:
                self._cache.pop(next(iter(self._cache)))
            self._cache[text] = vec
            return vec
        except Exception as exc:
            logger.debug("Query embedding failed: %s", exc)
            return None

    def delete(self, namespace: str, key: str) -> None:
        """Remove embeddings for a fact."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM embeddings WHERE namespace=? AND key=?", (namespace, key)
                )
        except sqlite3.OperationalError:
            pass  # table may not exist yet — deletion is a no-op

    def count(self) -> int:
        """Return total number of stored embeddings."""
        try:
            with self._connect() as conn:
                return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


# ── SemanticRetriever ─────────────────────────────────────────────────────────

class SemanticRetriever:
    """Two-bucket retrieval: FTS5 keyword + vector similarity, merged and deduped.

    When embeddings are available, combines:
      - FTS5 results (fast, exact/near-exact keyword match)
      - Semantic results (slower, concept/intent match)

    Deduplicates by key and merges scores (semantic score takes precedence
    when > 0.8, FTS5 takes precedence for exact keyword matches).
    """

    def __init__(self, memory, embedding_store: EmbeddingStore | None = None) -> None:
        """
        Args:
            memory:          MemoryDriver instance.
            embedding_store: EmbeddingStore instance (optional — degrades to FTS5 only).
        """
        self._memory = memory
        self._embeddings = embedding_store

    def retrieve(self, query: str, limit: int = 12) -> list[dict]:
        """Return ranked, deduplicated facts for a query."""
        from macroa.memory.retriever import retrieve as fts_retrieve

        # Always run FTS5 retrieval
        fts_results = fts_retrieve(query, self._memory)
        # Deduplicate on (namespace, key) to avoid cross-namespace collisions
        seen: set[tuple[str, str]] = {(f["namespace"], f["key"]) for f in fts_results}

        if self._embeddings is None:
            return fts_results[:limit]

        # Run semantic search
        semantic_hits = self._embeddings.search(query, limit=limit * 2)
        if not semantic_hits:
            return fts_results[:limit]

        # Merge: semantic results that FTS5 didn't find
        all_facts = list(fts_results)

        for ns, key, score in semantic_hits:
            if (ns, key) in seen:
                continue
            fact = self._memory.get(ns, key)
            if fact is None:
                continue
            all_facts.append({
                "namespace": ns,
                "key": key,
                "value": fact,
                "confidence": round(score, 3),
                "source": "semantic",
                "updated_at": 0.0,
                "pinned": False,
            })
            seen.add((ns, key))

        return all_facts[:limit]
