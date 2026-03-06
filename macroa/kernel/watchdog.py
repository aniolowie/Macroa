"""Watchdog — condition-based interrupt system for Macroa.

Complements the Scheduler (clock interrupt) with hardware-style interrupts:
observers watch system state and fire kernel.run() when something changes,
with no user input required.

Architecture:
    WatchdogObserver   — ABC: setup() + check() → payload | None
    FileChangeObserver — fires on file mtime change, creation, or deletion
    MemoryChangeObserver — fires when a /mem/<ns>/<key> fact changes
    WatchdogManager    — thread per observer, SQLite persistence, lifecycle

Each observer runs in a daemon thread polling at its own interval. When
check() returns a non-None string, that string is passed to kernel.run()
as a synthetic user input — routed, dispatched, and audited like any other.

Action templates support {var} interpolation:
    FileChangeObserver:   {path}, {event}, {content}, {timestamp}
    MemoryChangeObserver: {ns}, {key}, {value}, {old_value}, {timestamp}
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# kernel.run() signature minus return value
RunFn = Callable[[str, str], object]


# ── Observer metadata (persisted) ─────────────────────────────────────────────

@dataclass
class ObserverMeta:
    """Full record for one registered observer."""
    observer_id: str
    observer_type: str       # class name, used to reconstruct on restart
    config: dict             # type-specific JSON config
    action: str              # kernel.run() input template with {vars}
    session_id: str
    poll_interval: int = 30  # seconds between check() calls
    once: bool = False       # if True, remove observer after first trigger
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_triggered_at: float | None = None
    trigger_count: int = 0


# ── Observer base class ───────────────────────────────────────────────────────

class WatchdogObserver(ABC):
    """Base for all state-monitoring observers.

    Subclasses must track their own state to distinguish a CHANGE from a
    steady condition — check() should only return a payload when something
    actually changed since the last call.
    """

    def __init__(self, meta: ObserverMeta) -> None:
        self.meta = meta

    @property
    def observer_id(self) -> str:
        return self.meta.observer_id

    @property
    def poll_interval(self) -> int:
        return self.meta.poll_interval

    @abstractmethod
    def setup(self) -> None:
        """Record baseline state. Called once before the polling loop starts.
        Must NOT trigger even if the condition is currently met."""

    @abstractmethod
    def check(self) -> str | None:
        """Return formatted trigger payload if state changed, else None."""

    @abstractmethod
    def to_config(self) -> dict:
        """Serialisable config dict (stored in SQLite)."""

    @classmethod
    @abstractmethod
    def from_meta(cls, meta: ObserverMeta) -> WatchdogObserver:
        """Reconstruct observer from persisted ObserverMeta."""

    def _format_action(self, **kwargs) -> str:
        kwargs.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S"))
        kwargs.setdefault("observer_name", self.__class__.__name__)
        try:
            return self.meta.action.format(**kwargs)
        except KeyError:
            return self.meta.action  # return raw template on interpolation failure


# ── Built-in observers ────────────────────────────────────────────────────────

class FileChangeObserver(WatchdogObserver):
    """Fires when a file's mtime changes, the file appears, or is deleted.

    Default action template vars: {path}, {event}, {content}, {timestamp}
    Events: "created" | "modified" | "deleted"
    """

    DEFAULT_ACTION = (
        "Watchdog: file {path} was {event} at {timestamp}.\n"
        "Content preview:\n{content}"
    )

    def __init__(self, meta: ObserverMeta) -> None:
        super().__init__(meta)
        self._path = Path(meta.config["path"]).expanduser()
        self._last_mtime: float | None = None
        self._initialized = False

    def setup(self) -> None:
        # Record current state without triggering
        self._last_mtime = self._path.stat().st_mtime if self._path.exists() else None
        self._initialized = True

    def check(self) -> str | None:
        if not self._initialized:
            return None

        exists_now = self._path.exists()

        if not exists_now and self._last_mtime is not None:
            # File was deleted
            self._last_mtime = None
            return self._format_action(path=str(self._path), event="deleted", content="(deleted)")

        if not exists_now:
            return None  # still doesn't exist

        current_mtime = self._path.stat().st_mtime

        if self._last_mtime is None:
            # File appeared since setup
            self._last_mtime = current_mtime
            return self._format_action(
                path=str(self._path), event="created", content=self._read(),
            )

        if current_mtime != self._last_mtime:
            self._last_mtime = current_mtime
            return self._format_action(
                path=str(self._path), event="modified", content=self._read(),
            )

        return None

    def _read(self) -> str:
        try:
            text = self._path.read_text(encoding="utf-8", errors="replace")
            return text[:500] + ("…" if len(text) > 500 else "")
        except OSError:
            return "(unreadable)"

    def to_config(self) -> dict:
        return {"path": str(self._path)}

    @classmethod
    def from_meta(cls, meta: ObserverMeta) -> FileChangeObserver:
        return cls(meta)


class MemoryChangeObserver(WatchdogObserver):
    """Fires when a /mem/<namespace>/<key> fact changes value or is deleted.

    Default action template vars: {ns}, {key}, {value}, {old_value}, {timestamp}
    """

    DEFAULT_ACTION = (
        "Watchdog: memory fact {ns}/{key} changed "
        "from '{old_value}' to '{value}' at {timestamp}."
    )

    def __init__(self, meta: ObserverMeta, memory_driver=None) -> None:
        super().__init__(meta)
        self._ns = meta.config["namespace"]
        self._key = meta.config["key"]
        self._memory = memory_driver
        self._last_value: str | None = None
        self._initialized = False

    def setup(self) -> None:
        if self._memory:
            self._last_value = self._memory.get(self._ns, self._key)
        self._initialized = True

    def check(self) -> str | None:
        if not self._initialized or not self._memory:
            return None
        current = self._memory.get(self._ns, self._key)
        if current != self._last_value:
            old = self._last_value
            self._last_value = current
            return self._format_action(
                ns=self._ns,
                key=self._key,
                value=current if current is not None else "(deleted)",
                old_value=old if old is not None else "(none)",
            )
        return None

    def to_config(self) -> dict:
        return {"namespace": self._ns, "key": self._key}

    @classmethod
    def from_meta(cls, meta: ObserverMeta) -> MemoryChangeObserver:
        return cls(meta)  # memory_driver injected by WatchdogManager


# ── Manager ───────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, type[WatchdogObserver]] = {
    "FileChangeObserver": FileChangeObserver,
    "MemoryChangeObserver": MemoryChangeObserver,
}


class WatchdogManager:
    """Manages observer threads and SQLite persistence.

    Each observer runs in a daemon thread. Thread failure is logged but does
    not crash the kernel. Persisted observers are restarted on kernel init.
    """

    def __init__(
        self,
        db_path: Path,
        run_fn: RunFn,
        memory_driver=None,
    ) -> None:
        self._db_path = db_path
        self._run_fn = run_fn
        self._memory = memory_driver
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._observers: dict[str, WatchdogObserver] = {}
        self._lock = threading.Lock()
        self._init_db()

    # ── SQLite ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS observers (
                    id            TEXT PRIMARY KEY,
                    type          TEXT NOT NULL,
                    config        TEXT NOT NULL,
                    action        TEXT NOT NULL,
                    session_id    TEXT NOT NULL,
                    poll_interval INTEGER NOT NULL DEFAULT 30,
                    once          INTEGER NOT NULL DEFAULT 0,
                    enabled       INTEGER NOT NULL DEFAULT 1,
                    created_at    REAL NOT NULL,
                    last_triggered_at REAL,
                    trigger_count INTEGER NOT NULL DEFAULT 0
                )
            """)

    def _persist(self, meta: ObserverMeta) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO observers
                (id, type, config, action, session_id, poll_interval, once, enabled,
                 created_at, last_triggered_at, trigger_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                meta.observer_id, meta.observer_type,
                json.dumps(meta.config), meta.action,
                meta.session_id, meta.poll_interval,
                int(meta.once), int(meta.enabled),
                meta.created_at, meta.last_triggered_at,
                meta.trigger_count,
            ))

    def _record_trigger(self, observer_id: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE observers SET last_triggered_at=?, trigger_count=trigger_count+1 WHERE id=?",
                (time.time(), observer_id),
            )

    def _delete_row(self, observer_id: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM observers WHERE id=?", (observer_id,))

    def _load_all_rows(self, enabled_only: bool = False) -> list[ObserverMeta]:
        query = "SELECT id,type,config,action,session_id,poll_interval,once,enabled,created_at,last_triggered_at,trigger_count FROM observers"
        if enabled_only:
            query += " WHERE enabled=1"
        query += " ORDER BY created_at DESC"
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query).fetchall()
        return [ObserverMeta(
            observer_id=r[0], observer_type=r[1], config=json.loads(r[2]),
            action=r[3], session_id=r[4], poll_interval=r[5],
            once=bool(r[6]), enabled=bool(r[7]), created_at=r[8],
            last_triggered_at=r[9], trigger_count=r[10],
        ) for r in rows]

    # ── Observer construction ─────────────────────────────────────────────────

    def _build(self, meta: ObserverMeta) -> WatchdogObserver | None:
        cls = _REGISTRY.get(meta.observer_type)
        if cls is None:
            logger.warning("Watchdog: unknown observer type %r — skipping", meta.observer_type)
            return None
        if meta.observer_type == "MemoryChangeObserver":
            return MemoryChangeObserver(meta, self._memory)
        return cls.from_meta(meta)

    # ── Thread loop ───────────────────────────────────────────────────────────

    def _run_loop(self, observer: WatchdogObserver, stop: threading.Event) -> None:
        try:
            observer.setup()
        except Exception as exc:
            logger.error("Watchdog %s setup error: %s", observer.observer_id[:8], exc)
            return

        while not stop.wait(timeout=observer.poll_interval):
            try:
                payload = observer.check()
                if payload is not None:
                    logger.info("Watchdog %s triggered", observer.observer_id[:8])
                    self._record_trigger(observer.observer_id)
                    self._run_fn(payload, observer.meta.session_id)
                    if observer.meta.once:
                        self.delete(observer.observer_id)
                        return
            except Exception as exc:
                logger.error("Watchdog %s check error: %s", observer.observer_id[:8], exc)

    def _launch(self, observer: WatchdogObserver) -> None:
        stop = threading.Event()
        thread = threading.Thread(
            target=self._run_loop,
            args=(observer, stop),
            daemon=True,
            name=f"watchdog-{observer.observer_id[:8]}",
        )
        with self._lock:
            self._stop_events[observer.observer_id] = stop
            self._threads[observer.observer_id] = thread
            self._observers[observer.observer_id] = observer
        thread.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Reload and start all enabled persisted observers."""
        metas = self._load_all_rows(enabled_only=True)
        for meta in metas:
            observer = self._build(meta)
            if observer:
                self._launch(observer)
        if metas:
            logger.info("Watchdog: resumed %d observer(s)", len(metas))

    def add(
        self,
        observer_type: str,
        config: dict,
        action: str,
        session_id: str,
        poll_interval: int = 30,
        once: bool = False,
    ) -> ObserverMeta:
        """Register and immediately start a new observer."""
        if observer_type not in _REGISTRY:
            raise ValueError(f"Unknown observer type: {observer_type!r}. Available: {list(_REGISTRY)}")
        meta = ObserverMeta(
            observer_id=str(uuid.uuid4()),
            observer_type=observer_type,
            config=config,
            action=action,
            session_id=session_id,
            poll_interval=poll_interval,
            once=once,
        )
        self._persist(meta)
        observer = self._build(meta)
        if observer:
            self._launch(observer)
        return meta

    def delete(self, observer_id: str) -> bool:
        """Stop and remove an observer."""
        with self._lock:
            stop = self._stop_events.pop(observer_id, None)
            self._threads.pop(observer_id, None)
            self._observers.pop(observer_id, None)
        if stop:
            stop.set()
        self._delete_row(observer_id)
        return stop is not None

    def enable(self, observer_id: str, enabled: bool = True) -> bool:
        """Enable or disable an observer (does not stop running thread)."""
        with sqlite3.connect(self._db_path) as conn:
            result = conn.execute(
                "UPDATE observers SET enabled=? WHERE id=?", (int(enabled), observer_id)
            )
        return result.rowcount > 0

    def list_observers(self) -> list[ObserverMeta]:
        """Return all observers (enabled and disabled)."""
        return self._load_all_rows()

    def stop(self) -> None:
        """Signal all observer threads to stop."""
        with self._lock:
            events = list(self._stop_events.values())
        for event in events:
            event.set()
        logger.info("Watchdog: stopped all observers")
