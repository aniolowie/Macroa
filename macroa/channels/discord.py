"""Discord channel adapter — connects a Discord bot to kernel.run().

Setup:
  1. Create an app at discord.com/developers → "Bot" section → copy token
  2. Enable "Message Content Intent" under Privileged Gateway Intents
  3. Invite bot with scope: bot + permissions: Send Messages, Read Messages
  4. Set MACROA_DISCORD_TOKEN or pass token= to DiscordAdapter
  5. Run: macroa discord start

Each Discord user (user_id) gets a dedicated Macroa session.

Implementation notes:
  - Uses Discord's HTTP API + Gateway (WebSocket) for real-time events.
  - Falls back to a REST polling approach (channel message history) when
    the gateway is unavailable — simpler, no extra deps, but higher latency.
  - For production use, the gateway path is preferred.

Dependencies: httpx (already declared), websockets (optional, for gateway).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from macroa.channels.base import AdapterError, BaseAdapter

logger = logging.getLogger(__name__)

_API_BASE = "https://discord.com/api/v10"
_RETRY_WAIT = 5
_MAX_CONTENT = 2000   # Discord message limit


class DiscordAdapter(BaseAdapter):
    """Discord bot adapter.

    Uses the Discord Gateway (WebSocket) for real-time message delivery when
    `websockets` is available; otherwise falls back to REST long-polling of a
    single watched channel (less efficient, but zero extra deps).

    Gateway mode: handles READY + MESSAGE_CREATE events, responds inline.
    REST mode:    polls /channels/{channel_id}/messages every 2 s.
    """

    _platform = "discord"

    def __init__(
        self,
        token: str,
        run_fn: Callable[[str, str], Any],
        channel_ids: list[str] | None = None,
        allowed_users: set[str] | None = None,
    ) -> None:
        """
        Args:
            token:        Discord bot token.
            run_fn:       kernel.run(text, session_id) callable.
            channel_ids:  Channels to listen in. If None, responds to DMs only.
            allowed_users: Optional whitelist of Discord user IDs.
        """
        super().__init__(run_fn)
        self._token = token
        self._channel_ids = set(channel_ids) if channel_ids else set()
        self._allowed = allowed_users
        self._headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=30)
        self._last_message_ids: dict[str, str] = {}  # channel_id → last seen message id
        self._bot_id: str | None = None
        self._gateway_thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Try gateway first; fall back to REST polling."""
        # Identify ourselves
        try:
            me = self._get_me()
            self._bot_id = me["id"]
            logger.info("Discord adapter identified as @%s (ID %s)", me["username"], me["id"])
        except AdapterError as exc:
            logger.error("Discord: %s", exc)
            raise

        # Try WebSocket gateway
        try:
            import websockets  # noqa: F401
            self._gateway_thread = threading.Thread(
                target=self._gateway_loop,
                daemon=True,
                name="macroa-discord-gateway",
            )
            self._gateway_thread.start()
            logger.info("Discord adapter started (gateway mode)")
        except ImportError:
            logger.info("websockets not installed — Discord adapter using REST polling mode")
            super().start()

    # ── REST polling (fallback) ───────────────────────────────────────────────

    def _poll_once(self) -> list[dict]:
        messages: list[dict] = []
        for channel_id in self._channel_ids:
            try:
                msgs = self._fetch_new_messages(channel_id)
                messages.extend(msgs)
            except Exception as exc:
                logger.warning("Discord REST poll error for channel %s: %s", channel_id, exc)
        time.sleep(2)  # REST polling rate limit courtesy
        return messages

    def _fetch_new_messages(self, channel_id: str) -> list[dict]:
        params: dict = {"limit": 50}
        last_id = self._last_message_ids.get(channel_id)
        if last_id:
            params["after"] = last_id

        resp = self._client.get(
            f"{_API_BASE}/channels/{channel_id}/messages",
            headers=self._headers,
            params=params,
        )
        if resp.status_code == 401:
            raise AdapterError("Discord: invalid bot token")
        if not resp.is_success:
            return []

        all_msgs = resp.json()
        if not all_msgs:
            return []

        # Update last seen ID
        self._last_message_ids[channel_id] = all_msgs[0]["id"]

        # Filter: ignore bot messages, filter by allowed users
        inbound: list[dict] = []
        for m in reversed(all_msgs):  # oldest first
            author = m.get("author", {})
            if author.get("bot"):
                continue
            user_id = author.get("id", "")
            if self._allowed and user_id not in self._allowed:
                continue
            content = m.get("content", "").strip()
            if not content:
                continue
            inbound.append({
                "user_id": user_id,
                "channel_id": channel_id,
                "text": content,
                "username": author.get("username", ""),
            })
        return inbound

    def _send(self, user_id: str, text: str) -> None:
        """Send a message. In REST mode, user_id is used to determine channel."""
        # In REST polling mode we don't have DM channels by default; log a warning.
        # Subclasses can override to store channel_id per user.
        logger.warning("Discord REST send to user %s (no channel context): %r", user_id, text[:80])

    def _send_to_channel(self, channel_id: str, text: str) -> None:
        """Send a message to a specific channel."""
        chunks = _split_message(text, _MAX_CONTENT)
        for chunk in chunks:
            try:
                self._client.post(
                    f"{_API_BASE}/channels/{channel_id}/messages",
                    headers=self._headers,
                    json={"content": chunk},
                )
            except httpx.RequestError as exc:
                logger.warning("Discord send error: %s", exc)

    # ── Gateway (WebSocket) mode ──────────────────────────────────────────────

    def _gateway_loop(self) -> None:
        """Run the Discord Gateway event loop in a daemon thread."""
        import asyncio
        asyncio.run(self._async_gateway())

    async def _async_gateway(self) -> None:
        import asyncio

        import websockets

        # Get Gateway URL
        try:
            resp = self._client.get(f"{_API_BASE}/gateway/bot", headers=self._headers)
            gateway_url = resp.json().get("url", "wss://gateway.discord.gg") + "/?v=10&encoding=json"
        except Exception as exc:
            logger.error("Discord: failed to get gateway URL: %s", exc)
            return

        heartbeat_interval: float = 41.25  # default, overridden by HELLO
        sequence: int | None = None

        async def _heartbeat(ws, interval: float) -> None:
            while True:
                await asyncio.sleep(interval / 1000)
                await ws.send(json.dumps({"op": 1, "d": sequence}))

        try:
            async with websockets.connect(gateway_url) as ws:
                hb_task: asyncio.Task | None = None

                while not self._stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=heartbeat_interval / 1000 + 5)
                    except TimeoutError:
                        continue

                    payload = json.loads(raw)
                    op = payload.get("op")
                    data = payload.get("d", {})
                    t = payload.get("t")

                    if payload.get("s") is not None:
                        sequence = payload["s"]

                    if op == 10:  # HELLO — start heartbeating + identify
                        heartbeat_interval = data["heartbeat_interval"]
                        if hb_task:
                            hb_task.cancel()
                        hb_task = asyncio.create_task(
                            _heartbeat(ws, heartbeat_interval)
                        )
                        # Identify
                        await ws.send(json.dumps({
                            "op": 2,
                            "d": {
                                "token": self._token,
                                "intents": 33280,  # GUILDS + GUILD_MESSAGES + MESSAGE_CONTENT
                                "properties": {"os": "linux", "browser": "macroa", "device": "macroa"},
                            },
                        }))

                    elif op == 11:  # Heartbeat ACK
                        pass

                    elif op == 0 and t == "READY":
                        self._bot_id = data["user"]["id"]
                        logger.info("Discord gateway READY as %s", data["user"]["username"])

                    elif op == 0 and t == "MESSAGE_CREATE":
                        self._on_message(data)

        except Exception as exc:
            logger.error("Discord gateway error: %s — reconnecting in %ds", exc, _RETRY_WAIT)
            time.sleep(_RETRY_WAIT)
            if not self._stop.is_set():
                threading.Thread(target=self._gateway_loop, daemon=True).start()

    def _on_message(self, data: dict) -> None:
        """Handle a MESSAGE_CREATE gateway event."""
        author = data.get("author", {})
        if author.get("bot") or author.get("id") == self._bot_id:
            return

        user_id = str(author.get("id", ""))
        channel_id = str(data.get("channel_id", ""))
        content = data.get("content", "").strip()

        if not content or not user_id:
            return

        # Allowed users check
        if self._allowed and user_id not in self._allowed:
            return

        # Channel filter (if configured)
        if self._channel_ids and channel_id not in self._channel_ids:
            return

        # Handle special commands
        if content.startswith("/macroa help"):
            self._send_to_channel(channel_id, (
                "**Macroa** — Personal AI OS\n"
                "• Just chat naturally — I remember our conversations\n"
                "• `/macroa help` — this message\n"
                "• `/macroa clear` — reset context"
            ))
            return

        if content.startswith("/macroa clear"):
            import macroa.kernel as kernel
            session_id = self._get_session(user_id)
            kernel.clear_session(session_id)
            self._send_to_channel(channel_id, "Context cleared.")
            return

        # Route to kernel
        session_id = self._get_session(user_id)
        try:
            result = self._run(content, session_id)
            reply = result.output or result.error or "(no response)"
        except Exception as exc:
            logger.error("Discord handler error: %s", exc)
            reply = "Something went wrong — please try again."

        self._send_to_channel(channel_id, reply)

    # ── API helpers ───────────────────────────────────────────────────────────

    def _get_me(self) -> dict:
        try:
            resp = self._client.get(f"{_API_BASE}/users/@me", headers=self._headers)
        except httpx.RequestError as exc:
            raise AdapterError(f"Discord network error: {exc}") from exc
        if resp.status_code == 401:
            raise AdapterError("Discord: invalid bot token (401)")
        if not resp.is_success:
            raise AdapterError(f"Discord API error {resp.status_code}")
        return resp.json()

    def validate_token(self) -> dict:
        """Validate the bot token and return bot info."""
        return self._get_me()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_message(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
