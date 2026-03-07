"""Base class for all channel adapters."""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class AdapterError(Exception):
    """Raised when an adapter fails to initialise or encounters a fatal error."""


class BaseAdapter(ABC):
    """Common lifecycle and threading for all channel adapters.

    Subclasses implement:
      _poll_once()  — fetch the next batch of inbound messages
      _send()       — send a reply back to the platform
      _platform     — string name (e.g. "telegram", "discord")
    """

    _platform: str = "base"

    def __init__(self, run_fn: Callable[[str, str], Any]) -> None:
        """
        Args:
            run_fn: callable with signature (text: str, session_id: str) → SkillResult
                    Typically kernel.run.
        """
        self._run = run_fn
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sessions: dict[str, str] = {}  # platform_user_id → kernel session_id

    # ── Public lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the polling loop in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"macroa-{self._platform}",
        )
        self._thread.start()
        logger.info("%s adapter started", self._platform.capitalize())

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self._stop.set()
        logger.info("%s adapter stopped", self._platform.capitalize())

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Session management ────────────────────────────────────────────────────

    def _get_session(self, user_id: str) -> str:
        """Return (or create) the kernel session for a platform user."""
        if user_id not in self._sessions:
            import macroa.kernel as kernel
            session_name = f"{self._platform}_{user_id}"
            self._sessions[user_id] = kernel.resolve_session(session_name)
        return self._sessions[user_id]

    # ── Subclass contract ─────────────────────────────────────────────────────

    @abstractmethod
    def _poll_once(self) -> list[dict]:
        """Return a list of inbound message dicts: {"user_id": ..., "text": ...}."""

    @abstractmethod
    def _send(self, user_id: str, text: str) -> None:
        """Send a reply to the given user on the platform."""

    # ── Internal loop ─────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        logger.info("%s adapter polling loop started", self._platform)
        while not self._stop.wait(timeout=1.0):
            try:
                messages = self._poll_once()
                for msg in messages:
                    self._handle(msg)
            except AdapterError as exc:
                logger.error("%s adapter fatal error: %s — stopping", self._platform, exc)
                break
            except Exception as exc:
                logger.warning("%s adapter poll error: %s", self._platform, exc)

    def _handle(self, msg: dict) -> None:
        """Route one inbound message to kernel.run() and send the reply."""
        user_id = str(msg["user_id"])
        text = msg.get("text", "").strip()
        if not text:
            return

        session_id = self._get_session(user_id)
        logger.debug("%s message from %s: %r", self._platform, user_id, text[:80])

        try:
            result = self._run(text, session_id)
            reply = result.output or result.error or "(no response)"
        except Exception as exc:
            logger.error("%s handler error for user %s: %s", self._platform, user_id, exc)
            reply = "Sorry, something went wrong. Please try again."

        try:
            self._send(user_id, reply)
        except Exception as exc:
            logger.error("%s send error for user %s: %s", self._platform, user_id, exc)
