"""Channel adapters — bridge external messaging platforms to kernel.run().

Each adapter:
  - Polls or listens for inbound messages
  - Routes each message to kernel.run() under a per-user session
  - Sends the AI's response back to the platform
  - Runs in a daemon thread (non-blocking for the caller)

Available adapters:
  TelegramAdapter  — Telegram Bot API (long-polling)
  DiscordAdapter   — Discord Bot API (gateway or HTTP polling)
"""

from macroa.channels.base import BaseAdapter, AdapterError  # noqa: F401
from macroa.channels.telegram import TelegramAdapter  # noqa: F401
from macroa.channels.discord import DiscordAdapter  # noqa: F401

__all__ = ["BaseAdapter", "AdapterError", "TelegramAdapter", "DiscordAdapter"]
