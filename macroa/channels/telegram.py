"""Telegram channel adapter — connects a Telegram Bot to kernel.run().

Setup:
  1. Create a bot via @BotFather → copy the token
  2. Set MACROA_TELEGRAM_TOKEN or pass token= to TelegramAdapter
  3. Run: macroa telegram start

Each Telegram user (chat_id) gets a dedicated Macroa session so the AI
maintains per-user memory, reminders, and context.

Special commands handled before routing to the kernel:
  /start   — welcome message
  /help    — show capabilities
  /clear   — wipe context for this chat

API: Telegram Bot API v6+ (long-polling via getUpdates).
Dependency: httpx (already a declared project dep).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from macroa.channels.base import AdapterError, BaseAdapter

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}"
_TIMEOUT = 30      # long-poll timeout in seconds
_MAX_TEXT = 4096   # Telegram message limit
_RETRY_WAIT = 5    # seconds to wait after a network error


class TelegramAdapter(BaseAdapter):
    """Long-poll Telegram Bot adapter.

    Long-polling works as follows:
      GET /getUpdates?offset=<next>&timeout=30
    Returns up to 100 pending updates, each with a message dict.
    The `offset` parameter ACKs all previous updates.
    """

    _platform = "telegram"

    def __init__(
        self,
        token: str,
        run_fn: Callable[[str, str], Any],
        allowed_users: set[str] | None = None,
    ) -> None:
        """
        Args:
            token:         Telegram Bot API token (from @BotFather).
            run_fn:        kernel.run(text, session_id) callable.
            allowed_users: Optional whitelist of Telegram user IDs (strings).
                           If None, any user can interact with the bot.
        """
        super().__init__(run_fn)
        self._token = token
        self._base = _API_BASE.format(token=token)
        self._offset: int = 0
        self._allowed = allowed_users
        self._client = httpx.Client(timeout=_TIMEOUT + 5)

    # ── Platform overrides ────────────────────────────────────────────────────

    def _poll_once(self) -> list[dict]:
        """Long-poll for the next batch of updates."""
        try:
            resp = self._client.get(
                f"{self._base}/getUpdates",
                params={"offset": self._offset, "timeout": _TIMEOUT, "allowed_updates": '["message"]'},
            )
        except httpx.RequestError as exc:
            logger.warning("Telegram poll network error: %s — retrying in %ds", exc, _RETRY_WAIT)
            time.sleep(_RETRY_WAIT)
            return []

        if resp.status_code == 401:
            raise AdapterError("Telegram: invalid bot token (401 Unauthorized)")
        if not resp.is_success:
            logger.warning("Telegram API error %d — retrying", resp.status_code)
            time.sleep(_RETRY_WAIT)
            return []

        data = resp.json()
        if not data.get("ok"):
            logger.warning("Telegram getUpdates not OK: %s", data)
            return []

        updates = data.get("result", [])
        messages: list[dict] = []

        for upd in updates:
            self._offset = upd["update_id"] + 1  # ACK this update
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            chat_id = str(msg["chat"]["id"])
            text = msg.get("text", "").strip()
            if not text:
                continue
            from_user = msg.get("from", {})
            user_id = str(from_user.get("id", chat_id))

            if self._allowed and user_id not in self._allowed:
                self._send(chat_id, "Sorry, I'm not configured to respond to you.")
                continue

            messages.append({
                "user_id": chat_id,   # use chat_id as session key (works for groups too)
                "from_user_id": user_id,
                "text": text,
                "first_name": from_user.get("first_name", ""),
            })

        return messages

    def _send(self, user_id: str, text: str) -> None:
        """Send a text message to a Telegram chat."""
        # Telegram has a 4096-char limit; split if needed
        chunks = _split_message(text, _MAX_TEXT)
        for chunk in chunks:
            try:
                self._client.post(
                    f"{self._base}/sendMessage",
                    json={"chat_id": user_id, "text": chunk, "parse_mode": "Markdown"},
                )
            except httpx.RequestError as exc:
                logger.warning("Telegram send error: %s", exc)

    # ── Special command handling ──────────────────────────────────────────────

    def _handle(self, msg: dict) -> None:
        """Handle /start, /help, /clear before routing to kernel."""
        text = msg.get("text", "")
        user_id = str(msg["user_id"])
        first_name = msg.get("first_name", "")

        if text.startswith("/start"):
            self._send(user_id, (
                f"Hello{', ' + first_name if first_name else ''}! "
                "I'm Macroa, your personal AI OS.\n\n"
                "Just send me a message and I'll respond. I remember our conversations.\n\n"
                "Commands: /help /clear"
            ))
            return

        if text.startswith("/help"):
            self._send(user_id, (
                "*Macroa Commands*\n"
                "• /start — welcome\n"
                "• /help — this message\n"
                "• /clear — reset conversation context\n\n"
                "Just type naturally — I can answer questions, set reminders, "
                "run research, write code, and remember facts about you."
            ))
            return

        if text.startswith("/clear"):
            import macroa.kernel as kernel
            session_id = self._get_session(user_id)
            kernel.clear_session(session_id)
            self._send(user_id, "Context cleared. Fresh start!")
            return

        # Route to kernel
        super()._handle(msg)

    def validate_token(self) -> dict:
        """Call getMe to verify the token and return bot info."""
        try:
            resp = self._client.get(f"{self._base}/getMe")
            if resp.status_code == 401:
                raise AdapterError("Invalid Telegram token (401)")
            data = resp.json()
            if not data.get("ok"):
                raise AdapterError(f"Telegram getMe failed: {data}")
            return data["result"]
        except httpx.RequestError as exc:
            raise AdapterError(f"Telegram network error: {exc}") from exc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_message(text: str, max_len: int) -> list[str]:
    """Split a long message into chunks at paragraph boundaries where possible."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to split at last newline before max_len
        split_at = text.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
