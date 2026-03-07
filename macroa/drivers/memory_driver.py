"""Memory driver — three-layer architecture mirroring human memory.

Working memory  → ContextManager (in-RAM rolling deque, already in kernel/context.py)
Semantic memory → facts table    (what the user told us; persistent, queryable)
Episodic memory → episodes table (session summaries; what happened and when)

Schema version: 3
  v1 → v2: renamed 'memory' table to 'facts', added confidence/source/expires_at/created_at
  v2 → v3: added 'pinned' column to facts; added FTS5 virtual table + sync triggers
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CURRENT_VERSION = 3


@dataclass
class Fact:
    namespace: str
    key: str
    value: str
    confidence: float = 1.0
    source: str = "user"
    pinned: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None


@dataclass
class Episode:
    session_id: str
    summary: str
    tags: list[str] = field(default_factory=list)
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    id: int | None = None


def _fts5_query(text: str) -> str:
    """Convert free text to a safe FTS5 MATCH expression.

    Strips FTS5 special characters and builds an OR-across-words query so
    partial matches still surface relevant facts.
    """
    sanitised = re.sub(r'["*()^~+\-:]', " ", text)
    words = [w for w in sanitised.split() if len(w) >= 2]
    if not words:
        return '""'
    # Double-quote each term so FTS5 treats them as phrase literals, not operators
    return " OR ".join(f'"{w}"' for w in words[:8])


class MemoryDriver:
    def __init__(self, backend: str = "sqlite", db_path: Path | None = None) -> None:
        self._backend = backend
        # Optional embedding store — set via .set_embedding_store() after construction
        self._embedding_store = None
        if backend == "sqlite":
            self._db_path = db_path or (Path.home() / ".macroa" / "memory.db")
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()
        else:
            self._json_path = db_path or (Path.home() / ".macroa" / "memory.json")
            self._json_path.parent.mkdir(parents=True, exist_ok=True)

    def set_embedding_store(self, store) -> None:
        """Attach an EmbeddingStore for async vector indexing on fact writes."""
        self._embedding_store = store

    # ------------------------------------------------------------------ init / migration

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version    INTEGER NOT NULL,
                    applied_at REAL    NOT NULL
                )
            """)

            version = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0] or 0

            if version < 1:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS memory (
                        namespace  TEXT NOT NULL,
                        key        TEXT NOT NULL,
                        value      TEXT NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (namespace, key)
                    )
                """)

            if version < 2:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS facts (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        namespace   TEXT    NOT NULL DEFAULT 'user',
                        key         TEXT    NOT NULL,
                        value       TEXT    NOT NULL,
                        confidence  REAL    NOT NULL DEFAULT 1.0,
                        source      TEXT    NOT NULL DEFAULT 'user',
                        created_at  REAL    NOT NULL,
                        updated_at  REAL    NOT NULL,
                        expires_at  REAL,
                        UNIQUE(namespace, key)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS episodes (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id  TEXT NOT NULL,
                        summary     TEXT NOT NULL,
                        tags        TEXT NOT NULL DEFAULT '[]',
                        turn_count  INTEGER NOT NULL DEFAULT 0,
                        created_at  REAL NOT NULL
                    )
                """)

                if version >= 1:
                    conn.execute("""
                        INSERT OR IGNORE INTO facts
                            (namespace, key, value, confidence, source, created_at, updated_at)
                        SELECT namespace, key, value, 1.0, 'user', updated_at, updated_at
                        FROM memory
                    """)

                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_ns_key ON facts(namespace, key)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_expires "
                    "ON facts(expires_at) WHERE expires_at IS NOT NULL"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at)"
                )
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (2, time.time()),
                )

            if version < 3:
                # Add pinned column (safe to run even if column already exists via try/except)
                try:
                    conn.execute(
                        "ALTER TABLE facts ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
                    )
                except sqlite3.OperationalError:
                    pass  # column already present (idempotent)

                conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_pinned ON facts(pinned)")

                # FTS5 full-text search table — content-table mode keeps data in facts,
                # FTS5 shadow tables only store the index.
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                        key,
                        value,
                        content='facts',
                        content_rowid='id',
                        tokenize='porter unicode61'
                    )
                """)

                # Triggers to keep FTS5 index in sync with facts table
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS facts_fts_ai
                    AFTER INSERT ON facts BEGIN
                        INSERT INTO facts_fts(rowid, key, value)
                        VALUES (new.id, new.key, new.value);
                    END
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS facts_fts_ad
                    AFTER DELETE ON facts BEGIN
                        INSERT INTO facts_fts(facts_fts, rowid, key, value)
                        VALUES ('delete', old.id, old.key, old.value);
                    END
                """)
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS facts_fts_au
                    AFTER UPDATE ON facts BEGIN
                        INSERT INTO facts_fts(facts_fts, rowid, key, value)
                        VALUES ('delete', old.id, old.key, old.value);
                        INSERT INTO facts_fts(rowid, key, value)
                        VALUES (new.id, new.key, new.value);
                    END
                """)

                # Backfill FTS5 from existing facts
                conn.execute(
                    "INSERT INTO facts_fts(rowid, key, value) SELECT id, key, value FROM facts"
                )

                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (3, time.time()),
                )

    # ------------------------------------------------------------------ semantic memory (facts)

    def set(self, namespace: str, key: str, value: str) -> None:
        """Store a fact. Shorthand for set_fact() with default metadata."""
        self.set_fact(namespace, key, value)

    def set_fact(
        self,
        namespace: str,
        key: str,
        value: str,
        *,
        confidence: float = 1.0,
        source: str = "user",
        expires_at: float | None = None,
        pinned: bool = False,
    ) -> None:
        """Store a semantic fact with full metadata."""
        if self._backend == "sqlite":
            now = time.time()
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO facts
                        (namespace, key, value, confidence, source, created_at, updated_at,
                         expires_at, pinned)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        value      = excluded.value,
                        confidence = excluded.confidence,
                        source     = excluded.source,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at,
                        pinned     = excluded.pinned
                """, (namespace, key, value, confidence, source, now, now, expires_at,
                      int(pinned)))
        else:
            data = self._json_load()
            data.setdefault(namespace, {})[key] = {
                "value": value,
                "confidence": confidence,
                "source": source,
                "pinned": pinned,
                "created_at": time.time(),
                "updated_at": time.time(),
                "expires_at": expires_at,
            }
            self._json_save(data)

        # Queue for background vector embedding (non-blocking, best-effort)
        if self._embedding_store is not None:
            try:
                self._embedding_store.queue_embed(namespace, key, f"{key}: {value}")
            except Exception:
                pass

    def get(self, namespace: str, key: str) -> str | None:
        """Retrieve a fact value by exact key. Returns None if missing or expired."""
        if self._backend == "sqlite":
            now = time.time()
            with self._connect() as conn:
                row = conn.execute("""
                    SELECT value FROM facts
                    WHERE namespace=? AND key=?
                      AND (expires_at IS NULL OR expires_at > ?)
                """, (namespace, key, now)).fetchone()
            return row[0] if row else None
        else:
            data = self._json_load()
            entry = data.get(namespace, {}).get(key)
            if not entry:
                return None
            if entry.get("expires_at") and entry["expires_at"] <= time.time():
                return None
            return entry["value"]

    def get_fact(self, namespace: str, key: str) -> Fact | None:
        """Retrieve a full Fact object including metadata."""
        if self._backend != "sqlite":
            value = self.get(namespace, key)
            return Fact(namespace=namespace, key=key, value=value) if value else None
        now = time.time()
        with self._connect() as conn:
            row = conn.execute("""
                SELECT namespace, key, value, confidence, source, pinned,
                       created_at, updated_at, expires_at
                FROM facts WHERE namespace=? AND key=?
                  AND (expires_at IS NULL OR expires_at > ?)
            """, (namespace, key, now)).fetchone()
        if not row:
            return None
        return Fact(
            namespace=row[0], key=row[1], value=row[2],
            confidence=row[3], source=row[4], pinned=bool(row[5]),
            created_at=row[6], updated_at=row[7], expires_at=row[8],
        )

    def pin(self, namespace: str, key: str, *, pinned: bool = True) -> bool:
        """Set or clear the pinned flag on a fact. Returns True if the fact exists."""
        if self._backend != "sqlite":
            return False
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE facts SET pinned=? WHERE namespace=? AND key=?",
                (int(pinned), namespace, key),
            )
        return cur.rowcount > 0

    def delete(self, namespace: str, key: str) -> bool:
        if self._backend == "sqlite":
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM facts WHERE namespace=? AND key=?", (namespace, key)
                )
            return cur.rowcount > 0
        else:
            data = self._json_load()
            ns = data.get(namespace, {})
            if key in ns:
                del ns[key]
                self._json_save(data)
                return True
            return False

    def search(self, query: str, namespace: str | None = None) -> list[dict]:
        """Case-insensitive substring match on key + value. Excludes expired facts."""
        q = query.lower()
        now = time.time()
        if self._backend == "sqlite":
            with self._connect() as conn:
                base = """
                    SELECT namespace, key, value, confidence, source, updated_at, pinned
                    FROM facts
                    WHERE (expires_at IS NULL OR expires_at > ?)
                      AND (LOWER(key) LIKE ? OR LOWER(value) LIKE ?)
                """
                params: list[Any] = [now, f"%{q}%", f"%{q}%"]
                if namespace:
                    base += " AND namespace=?"
                    params.append(namespace)
                base += " ORDER BY pinned DESC, confidence DESC, updated_at DESC"
                rows = conn.execute(base, params).fetchall()
            return [
                {
                    "namespace": r[0], "key": r[1], "value": r[2],
                    "confidence": r[3], "source": r[4], "updated_at": r[5],
                    "pinned": bool(r[6]),
                }
                for r in rows
            ]
        else:
            data = self._json_load()
            results = []
            for ns, entries in data.items():
                if namespace and ns != namespace:
                    continue
                for key, meta in entries.items():
                    if meta.get("expires_at") and meta["expires_at"] <= now:
                        continue
                    if q in key.lower() or q in meta["value"].lower():
                        results.append({
                            "namespace": ns, "key": key,
                            "value": meta["value"],
                            "confidence": meta.get("confidence", 1.0),
                            "source": meta.get("source", "user"),
                            "updated_at": meta.get("updated_at", 0),
                            "pinned": meta.get("pinned", False),
                        })
            return results

    def search_fts(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search using FTS5 BM25 ranking. Falls back to LIKE on error."""
        if self._backend != "sqlite":
            return self.search(query)

        fts_q = _fts5_query(query)
        now = time.time()
        try:
            with self._connect() as conn:
                rows = conn.execute("""
                    SELECT f.namespace, f.key, f.value, f.confidence,
                           f.source, f.updated_at, f.pinned
                    FROM facts_fts
                    JOIN facts f ON facts_fts.rowid = f.id
                    WHERE facts_fts MATCH ?
                      AND (f.expires_at IS NULL OR f.expires_at > ?)
                    ORDER BY rank, f.confidence DESC
                    LIMIT ?
                """, (fts_q, now, limit)).fetchall()
            return [
                {
                    "namespace": r[0], "key": r[1], "value": r[2],
                    "confidence": r[3], "source": r[4], "updated_at": r[5],
                    "pinned": bool(r[6]),
                }
                for r in rows
            ]
        except sqlite3.OperationalError:
            # FTS5 not available or query error — fall back to LIKE search
            return self.search(query)

    def list_pinned(self, namespace: str | None = None) -> list[dict]:
        """Return all pinned facts, ordered by confidence then recency."""
        now = time.time()
        if self._backend == "sqlite":
            with self._connect() as conn:
                base = """
                    SELECT namespace, key, value, confidence, source, updated_at, pinned
                    FROM facts
                    WHERE pinned=1
                      AND (expires_at IS NULL OR expires_at > ?)
                """
                params: list[Any] = [now]
                if namespace:
                    base += " AND namespace=?"
                    params.append(namespace)
                base += " ORDER BY confidence DESC, updated_at DESC LIMIT 20"
                rows = conn.execute(base, params).fetchall()
            return [
                {
                    "namespace": r[0], "key": r[1], "value": r[2],
                    "confidence": r[3], "source": r[4], "updated_at": r[5],
                    "pinned": True,
                }
                for r in rows
            ]
        else:
            return [f for f in self.list_all(namespace) if f.get("pinned")]

    def list_all(self, namespace: str | None = None) -> list[dict]:
        """List all non-expired facts, pinned first then newest."""
        now = time.time()
        if self._backend == "sqlite":
            with self._connect() as conn:
                base = """
                    SELECT namespace, key, value, confidence, source, updated_at, pinned
                    FROM facts WHERE (expires_at IS NULL OR expires_at > ?)
                """
                params: list[Any] = [now]
                if namespace:
                    base += " AND namespace=?"
                    params.append(namespace)
                base += " ORDER BY pinned DESC, updated_at DESC"
                rows = conn.execute(base, params).fetchall()
            return [
                {
                    "namespace": r[0], "key": r[1], "value": r[2],
                    "confidence": r[3], "source": r[4], "updated_at": r[5],
                    "pinned": bool(r[6]),
                }
                for r in rows
            ]
        else:
            data = self._json_load()
            results = []
            for ns, entries in data.items():
                if namespace and ns != namespace:
                    continue
                for key, meta in entries.items():
                    if meta.get("expires_at") and meta["expires_at"] <= now:
                        continue
                    results.append({
                        "namespace": ns, "key": key,
                        "value": meta["value"],
                        "confidence": meta.get("confidence", 1.0),
                        "source": meta.get("source", "user"),
                        "updated_at": meta.get("updated_at", 0),
                        "pinned": meta.get("pinned", False),
                    })
            return sorted(results, key=lambda x: (not x["pinned"], -x["updated_at"]))

    def purge_expired(self) -> int:
        """Delete all expired facts. Returns count removed."""
        if self._backend != "sqlite":
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM facts WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (time.time(),),
            )
        return cur.rowcount

    # ------------------------------------------------------------------ episodic memory

    def add_episode(
        self,
        session_id: str,
        summary: str,
        tags: list[str] | None = None,
        turn_count: int = 0,
    ) -> int:
        """Record an episodic memory (session summary). Returns the new episode id."""
        if self._backend != "sqlite":
            return -1
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO episodes (session_id, summary, tags, turn_count, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, summary, json.dumps(tags or []), turn_count, now))
        return cur.lastrowid  # type: ignore[return-value]

    def get_episodes(
        self,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[Episode]:
        """Retrieve episodes, optionally filtered by session. Newest first."""
        if self._backend != "sqlite":
            return []
        with self._connect() as conn:
            if session_id:
                rows = conn.execute("""
                    SELECT id, session_id, summary, tags, turn_count, created_at
                    FROM episodes WHERE session_id=?
                    ORDER BY created_at DESC LIMIT ?
                """, (session_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, session_id, summary, tags, turn_count, created_at
                    FROM episodes ORDER BY created_at DESC LIMIT ?
                """, (limit,)).fetchall()
        return [
            Episode(
                id=r[0], session_id=r[1], summary=r[2],
                tags=json.loads(r[3] or "[]"),
                turn_count=r[4], created_at=r[5],
            )
            for r in rows
        ]

    def search_episodes(self, query: str, limit: int = 10) -> list[Episode]:
        """Substring search across episode summaries and tags."""
        if self._backend != "sqlite":
            return []
        q = f"%{query.lower()}%"
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, session_id, summary, tags, turn_count, created_at
                FROM episodes
                WHERE LOWER(summary) LIKE ? OR LOWER(tags) LIKE ?
                ORDER BY created_at DESC LIMIT ?
            """, (q, q, limit)).fetchall()
        return [
            Episode(
                id=r[0], session_id=r[1], summary=r[2],
                tags=json.loads(r[3] or "[]"),
                turn_count=r[4], created_at=r[5],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ human-readable export

    def export_markdown(self) -> str:
        """Generate a human-readable snapshot of all memory."""
        lines = ["# Macroa Memory Snapshot\n"]

        facts = self.list_all()
        if facts:
            lines.append("## Semantic Memory (Facts)\n")
            ns_groups: dict[str, list[dict]] = {}
            for f in facts:
                ns_groups.setdefault(f["namespace"], []).append(f)
            for ns, entries in ns_groups.items():
                lines.append(f"### {ns}\n")
                for e in entries:
                    pin_marker = " 📌" if e.get("pinned") else ""
                    conf = (
                        f" _(confidence: {e['confidence']:.0%})_"
                        if e["confidence"] < 1.0 else ""
                    )
                    lines.append(f"- **{e['key']}**: {e['value']}{conf}{pin_marker}\n")
            lines.append("")

        episodes = self.get_episodes(limit=50)
        if episodes:
            lines.append("## Episodic Memory (Sessions)\n")
            for ep in episodes:
                import datetime
                ts = datetime.datetime.fromtimestamp(ep.created_at).strftime("%Y-%m-%d %H:%M")
                tags = ", ".join(ep.tags) if ep.tags else "—"
                lines.append(f"**{ts}** ({ep.turn_count} turns) — tags: {tags}\n")
                lines.append(f"> {ep.summary}\n\n")

        return "".join(lines)

    # ------------------------------------------------------------------ json helpers

    def _json_load(self) -> dict:
        if not self._json_path.exists():
            return {}
        try:
            return json.loads(self._json_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _json_save(self, data: dict) -> None:
        self._json_path.write_text(json.dumps(data, indent=2))
