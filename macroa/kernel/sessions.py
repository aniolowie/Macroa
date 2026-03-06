"""Named session store — maps human names to UUIDs, persists context across restarts."""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from macroa.stdlib.schema import ContextEntry


@dataclass
class SessionMeta:
    session_id: str
    name: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turn_count: int = 0


class SessionStore:
    """
    SQLite-backed store for named sessions.

    Responsibilities:
    - Map human-readable session names → stable UUIDs
    - Persist context entries so sessions survive process restarts
    - Provide list/delete operations for the CLI
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    # ------------------------------------------------------------------ schema

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    name        TEXT UNIQUE NOT NULL,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL,
                    turn_count  INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS context_entries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    turn_id     TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    pinned      INTEGER NOT NULL DEFAULT 0,
                    skill_name  TEXT,
                    timestamp   REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_ctx_session ON context_entries(session_id);
            """)
            self._conn.commit()

    # ------------------------------------------------------------------ session management

    def get_or_create(self, name: str) -> SessionMeta:
        """Return existing session by name, or create a new one."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE name = ?", (name,)
            ).fetchone()
            if row:
                return SessionMeta(
                    session_id=row["session_id"],
                    name=row["name"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    turn_count=row["turn_count"],
                )
            session_id = str(uuid.uuid4())
            now = time.time()
            self._conn.execute(
                "INSERT INTO sessions (session_id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, name, now, now),
            )
            self._conn.commit()
            return SessionMeta(session_id=session_id, name=name, created_at=now, updated_at=now)

    def get_by_id(self, session_id: str) -> Optional[SessionMeta]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None
            return SessionMeta(
                session_id=row["session_id"],
                name=row["name"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                turn_count=row["turn_count"],
            )

    def list_sessions(self) -> list[SessionMeta]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [
                SessionMeta(
                    session_id=r["session_id"],
                    name=r["name"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    turn_count=r["turn_count"],
                )
                for r in rows
            ]

    def delete(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE name = ?", (name,))
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------ context persistence

    def save_context(self, session_id: str, entries: list[ContextEntry]) -> None:
        """Overwrite persisted context for a session (called after each turn)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM context_entries WHERE session_id = ?", (session_id,)
            )
            self._conn.executemany(
                """INSERT INTO context_entries
                   (session_id, turn_id, role, content, pinned, skill_name, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        session_id,
                        e.turn_id,
                        e.role,
                        e.content,
                        int(e.pinned),
                        e.skill_name,
                        e.timestamp,
                    )
                    for e in entries
                ],
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ?, turn_count = ? WHERE session_id = ?",
                (time.time(), len([e for e in entries if e.role == "user"]), session_id),
            )
            self._conn.commit()

    def load_context(self, session_id: str) -> list[ContextEntry]:
        """Load persisted context entries for a session."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM context_entries WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [
                ContextEntry(
                    turn_id=r["turn_id"],
                    role=r["role"],
                    content=r["content"],
                    pinned=bool(r["pinned"]),
                    skill_name=r["skill_name"],
                    timestamp=r["timestamp"],
                )
                for r in rows
            ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
