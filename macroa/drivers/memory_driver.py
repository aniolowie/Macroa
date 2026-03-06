"""Memory driver — three-layer architecture mirroring human memory.

Working memory  → ContextManager (in-RAM rolling deque, already in kernel/context.py)
Semantic memory → facts table    (what the user told us; persistent, queryable)
Episodic memory → episodes table (session summaries; what happened and when)

Why SQLite over .md:
  - Query power: WHERE confidence > 0.8 AND expires_at IS NULL (impossible in markdown)
  - ACID transactions: partial writes never corrupt state
  - Indexed search: O(log n) vs O(n) full-file scan
  - Schema versioning: ALTER TABLE is cleaner than re-parsing markdown
  - Zero server, zero cost — SQLite is in every Python install
  Hybrid: SQLite for machine access, export_markdown() for human review.

Schema version: 2
  v1 → v2: renamed 'memory' table to 'facts', added confidence/source/expires_at/created_at
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CURRENT_VERSION = 2


@dataclass
class Fact:
    namespace: str
    key: str
    value: str
    confidence: float = 1.0          # 0.0–1.0; inferred facts start lower
    source: str = "user"             # 'user' | 'inferred' | 'tool:<name>'
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None  # None = never expires


@dataclass
class Episode:
    session_id: str
    summary: str
    tags: list[str] = field(default_factory=list)
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    id: int | None = None


class MemoryDriver:
    def __init__(self, backend: str = "sqlite", db_path: Path | None = None) -> None:
        self._backend = backend
        if backend == "sqlite":
            self._db_path = db_path or (Path.home() / ".macroa" / "memory.db")
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_sqlite()
        else:
            self._json_path = db_path or (Path.home() / ".macroa" / "memory.json")
            self._json_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ init / migration

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent readers
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            # Schema version tracking
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
                # v1 → flat key-value (legacy, kept for migration detection)
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
                # v2 → semantic facts table with full metadata
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

                # Migrate v1 data if the old table had rows
                if version >= 1:
                    conn.execute("""
                        INSERT OR IGNORE INTO facts
                            (namespace, key, value, confidence, source, created_at, updated_at)
                        SELECT namespace, key, value, 1.0, 'user', updated_at, updated_at
                        FROM memory
                    """)

                # Indexes
                conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_ns_key ON facts(namespace, key)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_search ON facts(namespace, LOWER(key), LOWER(value))")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_expires ON facts(expires_at) WHERE expires_at IS NOT NULL")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at)")

                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (2, time.time()),
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
    ) -> None:
        """Store a semantic fact with full metadata."""
        if self._backend == "sqlite":
            now = time.time()
            with self._connect() as conn:
                conn.execute("""
                    INSERT INTO facts (namespace, key, value, confidence, source, created_at, updated_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(namespace, key) DO UPDATE SET
                        value      = excluded.value,
                        confidence = excluded.confidence,
                        source     = excluded.source,
                        updated_at = excluded.updated_at,
                        expires_at = excluded.expires_at
                """, (namespace, key, value, confidence, source, now, now, expires_at))
        else:
            data = self._json_load()
            data.setdefault(namespace, {})[key] = {
                "value": value,
                "confidence": confidence,
                "source": source,
                "created_at": time.time(),
                "updated_at": time.time(),
                "expires_at": expires_at,
            }
            self._json_save(data)

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
                SELECT namespace, key, value, confidence, source, created_at, updated_at, expires_at
                FROM facts WHERE namespace=? AND key=?
                  AND (expires_at IS NULL OR expires_at > ?)
            """, (namespace, key, now)).fetchone()
        if not row:
            return None
        return Fact(
            namespace=row[0], key=row[1], value=row[2],
            confidence=row[3], source=row[4],
            created_at=row[5], updated_at=row[6], expires_at=row[7],
        )

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
                    SELECT namespace, key, value, confidence, source, updated_at
                    FROM facts
                    WHERE (expires_at IS NULL OR expires_at > ?)
                      AND (LOWER(key) LIKE ? OR LOWER(value) LIKE ?)
                """
                params: list[Any] = [now, f"%{q}%", f"%{q}%"]
                if namespace:
                    base += " AND namespace=?"
                    params.append(namespace)
                base += " ORDER BY confidence DESC, updated_at DESC"
                rows = conn.execute(base, params).fetchall()
            return [
                {
                    "namespace": r[0], "key": r[1], "value": r[2],
                    "confidence": r[3], "source": r[4], "updated_at": r[5],
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
                        })
            return results

    def list_all(self, namespace: str | None = None) -> list[dict]:
        """List all non-expired facts, newest first."""
        now = time.time()
        if self._backend == "sqlite":
            with self._connect() as conn:
                base = """
                    SELECT namespace, key, value, confidence, source, updated_at
                    FROM facts WHERE (expires_at IS NULL OR expires_at > ?)
                """
                params: list[Any] = [now]
                if namespace:
                    base += " AND namespace=?"
                    params.append(namespace)
                base += " ORDER BY updated_at DESC"
                rows = conn.execute(base, params).fetchall()
            return [
                {
                    "namespace": r[0], "key": r[1], "value": r[2],
                    "confidence": r[3], "source": r[4], "updated_at": r[5],
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
                    })
            return sorted(results, key=lambda x: x["updated_at"], reverse=True)

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
        """Generate a human-readable snapshot of all memory. No LLM required."""
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
                    conf = f" _(confidence: {e['confidence']:.0%})_" if e["confidence"] < 1.0 else ""
                    lines.append(f"- **{e['key']}**: {e['value']}{conf}\n")
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
