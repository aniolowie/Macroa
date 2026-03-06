"""HeartbeatManager — the "always-on" pulse for persistent tools.

This is the foundation for the Phase 2 daemon/service mode. In Phase 1 (CLI),
start() is opt-in. In Phase 2 (background process), the kernel starts it
automatically and it runs until the process exits.

Persistent tools (manifest.persistent=True) register a heartbeat() method.
HeartbeatManager calls it on every tick, passing the current DriverBundle
so the tool can read memory, run shell commands, call the LLM, etc.

Example use cases for heartbeat():
  - Email monitor tool: check inbox every 60s, store new messages in memory
  - Reminder tool: check ~/.macroa/reminders.db, fire notifications when due
  - Health monitor: ping a server, store uptime stats in memory
  - Calendar sync: pull events, prepare a daily briefing

Architecture note:
  HeartbeatManager runs on a daemon thread — it dies automatically when
  the main process exits, no cleanup required for normal termination.
  For graceful shutdown, call stop() which sets the stop event and joins.
"""

from __future__ import annotations

import logging
import threading
import time

from macroa.stdlib.schema import DriverBundle
from macroa.tools.registry import ToolEntry, ToolRegistry

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60  # seconds


class HeartbeatManager:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        drivers: DriverBundle,
        interval: int = _DEFAULT_INTERVAL,
    ) -> None:
        self._registry = tool_registry
        self._drivers = drivers
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the heartbeat loop in a background daemon thread."""
        persistent = self._registry.persistent_tools()
        if not persistent:
            logger.debug("No persistent tools registered — heartbeat not started")
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="macroa-heartbeat",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Heartbeat started (interval=%ds, tools=%s)",
            self._interval,
            [e.manifest.name for e in persistent],
        )

    def stop(self) -> None:
        """Signal the heartbeat loop to stop and wait for it to finish."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._interval + 5)
        self._thread = None
        logger.info("Heartbeat stopped")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._tick()
            # Sleep in small increments so stop() is responsive
            for _ in range(self._interval * 10):
                if self._stop.is_set():
                    break
                time.sleep(0.1)

    def _tick(self) -> None:
        for entry in self._registry.persistent_tools():
            try:
                entry.tool.heartbeat(self._drivers)
            except Exception as exc:
                logger.warning(
                    "Heartbeat error in tool %s: %s", entry.manifest.name, exc
                )
