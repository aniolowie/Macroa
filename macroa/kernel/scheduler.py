"""
Kernel scheduler — cron-equivalent for Macroa.

Supported recurrence specs (stored in the `schedule` column):
  once:<unix_timestamp>          run once at the given epoch second
  every:<seconds>                run every N seconds starting from creation
  daily:<HH:MM>                  run every day at HH:MM local time
  cron:<min> <hr> <dom> <mon> <dow>  minimal cron expression (5-field)

The scheduler runs as a daemon thread; it polls the SQLite task table
every `poll_interval` seconds and fires any due tasks via `kernel.run()`.
All executions are recorded in the audit log through the normal kernel path.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    task_id: str
    label: str           # human-readable name
    command: str         # the input passed to kernel.run()
    schedule: str        # recurrence spec string
    session_id: str      # kernel session to run under
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_run_at: float | None = None
    next_run_at: float = field(default_factory=time.time)
    run_count: int = 0
    last_error: str | None = None


def _parse_next_run(schedule: str, now: float, last_run_at: float | None) -> float | None:
    """
    Calculate the next scheduled run time given the spec and current time.
    Returns None if the task should be removed (one-shot already fired).
    """
    if schedule.startswith("once:"):
        ts = float(schedule[5:])
        if last_run_at is not None:
            return None  # already ran
        return ts

    if schedule.startswith("every:"):
        interval = float(schedule[6:])
        base = last_run_at if last_run_at is not None else now
        return base + interval

    if schedule.startswith("daily:"):
        hhmm = schedule[6:]
        hh, mm = int(hhmm[:2]), int(hhmm[3:5])
        dt = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        ts = dt.timestamp()
        if ts <= now:
            ts = (dt + timedelta(days=1)).timestamp()
        return ts

    if schedule.startswith("cron:"):
        return _next_cron(schedule[5:], now)

    raise ValueError(f"Unknown schedule spec: {schedule!r}")


def _next_cron(expr: str, now: float) -> float:
    """Minimal 5-field cron: min hr dom mon dow (all local time, * = any)."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron expression must have 5 fields, got: {expr!r}")
    min_f, hr_f, dom_f, mon_f, dow_f = parts

    def _matches(field: str, value: int) -> bool:
        if field == "*":
            return True
        return int(field) == value

    # Search forward minute by minute (max 1 week)
    candidate = datetime.fromtimestamp(now).replace(second=0, microsecond=0)
    candidate += timedelta(minutes=1)
    limit = candidate + timedelta(weeks=1)

    while candidate < limit:
        if (
            _matches(mon_f, candidate.month)
            and _matches(dom_f, candidate.day)
            and _matches(dow_f, candidate.weekday())
            and _matches(hr_f, candidate.hour)
            and _matches(min_f, candidate.minute)
        ):
            return candidate.timestamp()
        candidate += timedelta(minutes=1)

    raise ValueError(f"cron expression {expr!r} never fires within one week")


class Scheduler:
    """
    SQLite-backed task scheduler with a background daemon thread.

    The caller is responsible for providing a `run_fn` — typically `kernel.run`.
    This keeps the scheduler decoupled from the kernel module to avoid circular
    imports while still allowing the scheduler to be tested in isolation.
    """

    def __init__(
        self,
        db_path: Path,
        run_fn: Callable[[str, str], object],
        poll_interval: float = 10.0,
    ) -> None:
        self._db_path = db_path
        self._run_fn = run_fn
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._create_schema()

    # ------------------------------------------------------------------ schema

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id      TEXT PRIMARY KEY,
                    label        TEXT NOT NULL,
                    command      TEXT NOT NULL,
                    schedule     TEXT NOT NULL,
                    session_id   TEXT NOT NULL,
                    enabled      INTEGER NOT NULL DEFAULT 1,
                    created_at   REAL NOT NULL,
                    last_run_at  REAL,
                    next_run_at  REAL NOT NULL,
                    run_count    INTEGER NOT NULL DEFAULT 0,
                    last_error   TEXT
                );
            """)
            self._conn.commit()

    # ------------------------------------------------------------------ public API

    def add(
        self,
        label: str,
        command: str,
        schedule: str,
        session_id: str,
    ) -> ScheduledTask:
        """Add a new scheduled task. Returns the created task."""
        now = time.time()
        next_run = _parse_next_run(schedule, now, last_run_at=None)
        if next_run is None:
            raise ValueError(f"Schedule {schedule!r} has no future run time")
        task = ScheduledTask(
            task_id=str(uuid.uuid4()),
            label=label,
            command=command,
            schedule=schedule,
            session_id=session_id,
            next_run_at=next_run,
            created_at=now,
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO scheduled_tasks
                   (task_id, label, command, schedule, session_id, enabled,
                    created_at, last_run_at, next_run_at, run_count, last_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task.task_id, task.label, task.command, task.schedule,
                    task.session_id, int(task.enabled), task.created_at,
                    task.last_run_at, task.next_run_at, task.run_count, task.last_error,
                ),
            )
            self._conn.commit()
        return task

    def list_tasks(self, include_disabled: bool = False) -> list[ScheduledTask]:
        where = "" if include_disabled else "WHERE enabled = 1"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM scheduled_tasks {where} ORDER BY next_run_at ASC"
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def delete(self, task_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM scheduled_tasks WHERE task_id = ?", (task_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def enable(self, task_id: str, enabled: bool = True) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE scheduled_tasks SET enabled = ? WHERE task_id = ?",
                (int(enabled), task_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ daemon

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="macroa-scheduler")
        self._thread.start()
        logger.info("Scheduler started (poll every %.0fs)", self._poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 2)
        with self._lock:
            self._conn.close()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------ internal

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Scheduler tick error")
            self._stop_event.wait(self._poll_interval)

    def _tick(self) -> None:
        now = time.time()
        with self._lock:
            due = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled = 1 AND next_run_at <= ?", (now,)
            ).fetchall()

        for row in due:
            task = self._row_to_task(row)
            self._fire(task, now)

    def _fire(self, task: ScheduledTask, now: float) -> None:
        logger.info("Scheduler firing task %r (%s)", task.label, task.task_id[:8])
        error: str | None = None
        try:
            self._run_fn(task.command, task.session_id)
        except Exception as exc:
            error = str(exc)
            logger.error("Scheduled task %r failed: %s", task.label, exc)

        # Emit REMINDER_FIRED for notification subscribers (e.g. REPL banner)
        if task.label.startswith("reminder:"):
            try:
                from macroa.kernel.events import Event, Events, bus
                bus.emit(Event(
                    event_type=Events.REMINDER_FIRED,
                    source="scheduler",
                    payload={
                        "task_id": task.task_id,
                        "label": task.label,
                        "message": task.command,
                        "fired_at": now,
                    },
                    session_id=task.session_id,
                ))
            except Exception:
                pass  # never let notification failure break the scheduler

        # Calculate next run or remove if one-shot
        next_run = _parse_next_run(task.schedule, now, last_run_at=now)

        with self._lock:
            if next_run is None:
                # One-shot: remove after firing
                self._conn.execute(
                    "DELETE FROM scheduled_tasks WHERE task_id = ?", (task.task_id,)
                )
            else:
                self._conn.execute(
                    """UPDATE scheduled_tasks
                       SET last_run_at = ?, next_run_at = ?, run_count = run_count + 1,
                           last_error = ?
                       WHERE task_id = ?""",
                    (now, next_run, error, task.task_id),
                )
            self._conn.commit()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> ScheduledTask:
        return ScheduledTask(
            task_id=row["task_id"],
            label=row["label"],
            command=row["command"],
            schedule=row["schedule"],
            session_id=row["session_id"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            last_run_at=row["last_run_at"],
            next_run_at=row["next_run_at"],
            run_count=row["run_count"],
            last_error=row["last_error"],
        )
