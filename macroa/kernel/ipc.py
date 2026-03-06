"""IPC — Inter-Process Communication bus for agent-to-agent messaging.

Agents running in separate sessions or threads can communicate in real-time
through named channels. A channel is a thread-safe FIFO queue: any agent can
write to it (emit) and any other agent can block-read from it (read_channel).

This is exposed to agents as three tools in tool_defs.py:
    emit(channel, message)             — write to a channel
    read_channel(channel, timeout=5)   — blocking read with timeout
    list_channels()                    — show active channels + pending counts

Every emit also fires an IPC_EMIT event on the kernel EventBus so the live
feed can display agent-to-agent traffic.

The IPCBus is a kernel singleton — channels are global across all sessions.
This is intentional: it allows agents in different sessions to coordinate
(e.g. a pentesting agent in session A signals a reporting agent in session B).

Channels are in-memory only — they do not survive kernel restarts. This is by
design: IPC is for real-time coordination, not persistence. Use the VFS
(/workspace/, /mem/) for results that need to outlive a session.
"""

from __future__ import annotations

import logging
import threading
import time
from queue import Empty, Queue

logger = logging.getLogger(__name__)

# Max messages buffered per channel before oldest are dropped
_CHANNEL_MAXSIZE = 256


class IPCBus:
    """Named-channel message bus. Kernel singleton — shared across all sessions."""

    def __init__(self) -> None:
        self._channels: dict[str, Queue] = {}
        self._lock = threading.Lock()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_or_create(self, channel: str) -> Queue:
        with self._lock:
            if channel not in self._channels:
                self._channels[channel] = Queue(maxsize=_CHANNEL_MAXSIZE)
                logger.debug("IPC: created channel %r", channel)
            return self._channels[channel]

    # ── Public API ────────────────────────────────────────────────────────────

    def emit(self, channel: str, content: str, source: str = "") -> None:
        """Write a message to a named channel.

        If the channel is full the oldest message is dropped to make room —
        a slow reader never blocks a fast writer.
        """
        q = self._get_or_create(channel)
        msg = {
            "channel": channel,
            "content": content,
            "source": source,
            "timestamp": time.time(),
        }
        if q.full():
            try:
                q.get_nowait()  # drop oldest
                logger.debug("IPC: channel %r full — dropped oldest message", channel)
            except Empty:
                pass
        q.put_nowait(msg)

        # Surface on EventBus for live feed visibility
        try:
            from macroa.kernel.events import Event, Events, bus
            bus.emit(Event(
                event_type=Events.IPC_EMIT,
                source=source or "ipc",
                payload={"channel": channel, "preview": content[:120]},
            ))
        except Exception:
            pass  # IPC must never fail due to EventBus issues

        logger.debug("IPC: emit → %r (%d chars)", channel, len(content))

    def read(self, channel: str, timeout: float = 5.0) -> dict | None:
        """Block until a message is available or timeout expires.

        Returns the message dict or None on timeout.
        Message dict keys: channel, content, source, timestamp.
        """
        q = self._get_or_create(channel)
        try:
            return q.get(timeout=timeout)
        except Empty:
            return None

    def list_channels(self) -> list[dict]:
        """Return all active channels with pending message counts."""
        with self._lock:
            return [
                {"channel": name, "pending": q.qsize()}
                for name, q in self._channels.items()
            ]

    def pending(self, channel: str) -> int:
        """Return number of messages waiting in a channel."""
        with self._lock:
            q = self._channels.get(channel)
            return q.qsize() if q else 0

    def flush(self, channel: str) -> int:
        """Discard all pending messages in a channel. Returns count dropped."""
        with self._lock:
            q = self._channels.get(channel)
            if not q:
                return 0
        dropped = 0
        while True:
            try:
                q.get_nowait()
                dropped += 1
            except Empty:
                break
        return dropped
