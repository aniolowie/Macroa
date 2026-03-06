"""Event bus — pub/sub backbone for the always-on OS.

Why this exists:
  In Phase 1 (CLI), the kernel is the only caller. In Phase 2 (daemon),
  multiple tools run concurrently, heartbeats fire on intervals, and
  components need to react to each other without tight coupling.
  The event bus is the message-passing layer that makes that possible.

Design:
  - Synchronous in Phase 1 (emit() calls handlers inline, in order)
  - Thread-safe (heartbeat thread can emit safely)
  - Module-level singleton (import and use anywhere)
  - Typed event constants so tooling can autocomplete

Built-in event types (emitted by the kernel):
  Events.KERNEL_RUN_START      — before kernel.run() dispatches
  Events.KERNEL_RUN_COMPLETE   — after kernel.run() returns
  Events.PLAN_CREATED          — planner decomposed a request
  Events.SKILL_DISPATCHED      — dispatcher called a skill/tool
  Events.ESCALATION            — tier promoted due to needs_reasoning
  Events.HEARTBEAT_TICK        — before each heartbeat cycle
  Events.MEMORY_SET            — a fact was stored
  Events.TOOL_LOADED           — a tool was registered

Tools can define and emit their own event types freely.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

HandlerFn = Callable[["Event"], None]


class Events:
    """Namespace of built-in event type constants."""
    KERNEL_RUN_START   = "kernel.run.start"
    KERNEL_RUN_COMPLETE = "kernel.run.complete"
    PLAN_CREATED       = "kernel.plan.created"
    SKILL_DISPATCHED   = "kernel.skill.dispatched"
    ESCALATION         = "kernel.escalation"
    HEARTBEAT_TICK     = "heartbeat.tick"
    MEMORY_SET         = "memory.set"
    TOOL_LOADED        = "tool.loaded"


@dataclass
class Event:
    event_type: str
    source: str                           # emitting component name
    payload: dict = field(default_factory=dict)
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """Thread-safe synchronous pub/sub bus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[HandlerFn]] = {}
        self._wildcard: list[HandlerFn] = []   # subscribed to every event
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: HandlerFn) -> None:
        """Register a handler for a specific event type."""
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler: HandlerFn) -> None:
        """Register a handler that receives every event (useful for audit/logging)."""
        with self._lock:
            self._wildcard.append(handler)

    def unsubscribe(self, event_type: str, handler: HandlerFn) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    def unsubscribe_all(self, handler: HandlerFn) -> None:
        with self._lock:
            try:
                self._wildcard.remove(handler)
            except ValueError:
                pass

    def emit(self, event: Event) -> None:
        """Dispatch event to all registered handlers. Handlers run synchronously.
        A failing handler is logged and skipped — it never kills the emit loop.
        """
        with self._lock:
            specific = list(self._handlers.get(event.event_type, []))
            wildcards = list(self._wildcard)

        for handler in specific + wildcards:
            try:
                handler(event)
            except Exception as exc:
                logger.warning(
                    "Event handler %r failed for %r: %s",
                    getattr(handler, "__name__", handler),
                    event.event_type,
                    exc,
                )

    def clear(self) -> None:
        """Remove all handlers. Useful for testing."""
        with self._lock:
            self._handlers.clear()
            self._wildcard.clear()


# Module-level singleton — import and use from anywhere
bus = EventBus()
