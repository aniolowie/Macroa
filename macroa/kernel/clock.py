"""Clock utilities — current time with user-aware timezone.

Single source of truth for "what time is it right now".
Injected into every chat system prompt so the LLM never has to guess.

Timezone resolution order:
  1. User memory fact "timezone" (namespace "user") — set by setup wizard or user
  2. System /etc/localtime symlink → IANA name
  3. Fallback: UTC
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from macroa.drivers.memory_driver import MemoryDriver


def get_user_timezone(memory: MemoryDriver | None = None) -> str:
    """Return the user's IANA timezone string."""
    # 1. Stored user preference
    if memory is not None:
        stored = memory.get("user", "timezone")
        if stored:
            try:
                ZoneInfo(stored)
                return stored
            except (ZoneInfoNotFoundError, KeyError):
                pass

    # 2. System timezone from /etc/localtime symlink
    try:
        if os.path.islink("/etc/localtime"):
            target = os.readlink("/etc/localtime")
            if "zoneinfo/" in target:
                iana = target.split("zoneinfo/", 1)[1]
                ZoneInfo(iana)  # validate
                return iana
    except Exception:
        pass  # /etc/localtime may not be a symlink or may be unreadable

    return "UTC"


def now_context(memory: MemoryDriver | None = None) -> str:
    """Return a single-line current datetime string for prompt injection.

    Example: "Current time: Saturday 07 March 2026, 03:13 (Europe/Paris)"
    """
    tz_name = get_user_timezone(memory)
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz=tz)
    return f"Current time: {now.strftime('%A %d %B %Y, %H:%M')} ({tz_name})"
