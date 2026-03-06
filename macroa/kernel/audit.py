"""Audit log — immutable record of everything the OS does.

Every kernel.run() call is recorded: input, which skill ran, which tier,
outcome, elapsed time, and plan step count. This is the foundation for:
  - Phase 2 web dashboard cost tracker
  - Debugging ("why did it call SONNET for that?")
  - Usage analytics (which skills run most?)
  - Cost estimation (tokens × price per tier)

Storage: SQLite table in ~/.macroa/audit.db (separate from memory.db so
  a memory wipe doesn't erase the audit trail).

The AuditLog subscribes to Events.KERNEL_RUN_COMPLETE via the event bus,
so no skill or tool needs to call it directly — it's automatic.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AuditEntry:
    turn_id: str
    session_id: str
    raw_input: str
    skill_name: str
    model_tier: str
    success: bool
    elapsed_ms: int
    plan_steps: int = 0        # 0 = single-step, N = planner used N steps
    error: str | None = None
    created_at: float = 0.0
    id: int | None = None


class AuditLog:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn_id     TEXT    NOT NULL,
                    session_id  TEXT    NOT NULL,
                    raw_input   TEXT    NOT NULL,
                    skill_name  TEXT    NOT NULL,
                    model_tier  TEXT    NOT NULL,
                    success     INTEGER NOT NULL,
                    elapsed_ms  INTEGER NOT NULL,
                    plan_steps  INTEGER NOT NULL DEFAULT 0,
                    error       TEXT,
                    created_at  REAL    NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_skill ON audit_log(skill_name)")

    def record(self, entry: AuditEntry) -> None:
        if not entry.created_at:
            entry.created_at = time.time()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO audit_log
                    (turn_id, session_id, raw_input, skill_name, model_tier,
                     success, elapsed_ms, plan_steps, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.turn_id, entry.session_id,
                entry.raw_input[:1000],   # cap at 1k chars
                entry.skill_name, entry.model_tier,
                int(entry.success), entry.elapsed_ms,
                entry.plan_steps, entry.error, entry.created_at,
            ))

    def recent(self, n: int = 20, session_id: str | None = None) -> list[AuditEntry]:
        with self._connect() as conn:
            if session_id:
                rows = conn.execute("""
                    SELECT id, turn_id, session_id, raw_input, skill_name,
                           model_tier, success, elapsed_ms, plan_steps, error, created_at
                    FROM audit_log WHERE session_id=?
                    ORDER BY created_at DESC LIMIT ?
                """, (session_id, n)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, turn_id, session_id, raw_input, skill_name,
                           model_tier, success, elapsed_ms, plan_steps, error, created_at
                    FROM audit_log ORDER BY created_at DESC LIMIT ?
                """, (n,)).fetchall()
        return [_row_to_entry(r) for r in rows]

    def stats(self) -> dict:
        """Aggregate usage stats — useful for the Phase 2 dashboard."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            by_skill = conn.execute("""
                SELECT skill_name, COUNT(*) as n, AVG(elapsed_ms) as avg_ms
                FROM audit_log GROUP BY skill_name ORDER BY n DESC
            """).fetchall()
            by_tier = conn.execute("""
                SELECT model_tier, COUNT(*) as n
                FROM audit_log GROUP BY model_tier ORDER BY n DESC
            """).fetchall()
            failures = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE success=0"
            ).fetchone()[0]
            plan_calls = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE plan_steps > 0"
            ).fetchone()[0]

        return {
            "total_runs": total,
            "failures": failures,
            "plan_calls": plan_calls,
            "by_skill": [{"skill": r[0], "count": r[1], "avg_ms": round(r[2])} for r in by_skill],
            "by_tier": [{"tier": r[0], "count": r[1]} for r in by_tier],
        }


def _row_to_entry(row: tuple) -> AuditEntry:
    return AuditEntry(
        id=row[0], turn_id=row[1], session_id=row[2],
        raw_input=row[3], skill_name=row[4], model_tier=row[5],
        success=bool(row[6]), elapsed_ms=row[7],
        plan_steps=row[8], error=row[9], created_at=row[10],
    )
