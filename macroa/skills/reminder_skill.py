"""Reminder skill — schedule a one-shot reminder from natural language.

Translates "remind me at 02:45 Paris time to call John" into a scheduled
kernel task using the existing Scheduler. The task fires kernel.run() with
the reminder text at the specified time, which routes back through the
normal pipeline (chat_skill will surface it to the user).

Supported time formats:
  "at HH:MM"                     — today (or tomorrow if time has passed), local TZ
  "at HH:MM <timezone>"          — explicit timezone (paris → Europe/Paris, etc.)
  "in N minutes / hours"         — relative offset
  "tomorrow at HH:MM"            — next day
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from macroa.stdlib.schema import (
    Context,
    DriverBundle,
    Intent,
    SkillManifest,
    SkillResult,
)

MANIFEST = SkillManifest(
    name="reminder_skill",
    description=(
        "Schedule a one-shot reminder at a specific time. "
        "Use when the user says 'remind me at HH:MM', 'remind me in N minutes/hours', "
        "'set a reminder', 'alert me at', 'wake me at'. "
        "Parameters: time (natural language string), message (what to remind), "
        "timezone (optional, e.g. 'Europe/Paris')."
    ),
    triggers=[
        "remind me", "set a reminder", "alert me at", "wake me at",
        "reminder at", "remind me at", "remind me in",
    ],
    model_tier=None,
    deterministic=True,
)

# Common city/region → IANA timezone aliases
_TZ_ALIASES: dict[str, str] = {
    "paris": "Europe/Paris",
    "london": "Europe/London",
    "berlin": "Europe/Berlin",
    "warsaw": "Europe/Warsaw",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "la": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "tokyo": "Asia/Tokyo",
    "sydney": "Australia/Sydney",
    "dubai": "Asia/Dubai",
    "utc": "UTC",
    "gmt": "GMT",
    "est": "America/New_York",
    "pst": "America/Los_Angeles",
    "cet": "Europe/Paris",
    "cest": "Europe/Paris",
}


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    raw = intent.raw

    # Try to get message / time from parameters (router may have parsed them)
    time_str = intent.parameters.get("time", "").strip()
    message = intent.parameters.get("message", "").strip()
    tz_param = intent.parameters.get("timezone", "").strip()

    # Fall back to parsing the raw input
    if not time_str:
        time_str = raw
    if not message:
        message = _extract_message(raw)

    # Parse the target timestamp
    try:
        target_ts, tz_used = _parse_time(time_str, tz_param)
    except ValueError as exc:
        return SkillResult(
            output="",
            success=False,
            error=f"Could not parse time from: {time_str!r} — {exc}",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )

    if target_ts <= time.time():
        # Time already passed today → schedule for tomorrow
        target_ts += 86_400

    label = f"reminder: {message[:40]}"
    schedule_spec = f"once:{int(target_ts)}"

    try:
        import macroa.kernel as kernel
        task = kernel.schedule_add(
            label=label,
            command=message,
            schedule=schedule_spec,
        )
    except Exception as exc:
        return SkillResult(
            output="",
            success=False,
            error=f"Failed to schedule reminder: {exc}",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )

    when = datetime.fromtimestamp(target_ts, tz=ZoneInfo(tz_used)).strftime("%H:%M %Z on %a %d %b")
    return SkillResult(
        output=f"Reminder set for {when}: {message}",
        success=True,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={"task_id": task.task_id, "target_ts": target_ts, "message": message},
    )


# ── time parsing ──────────────────────────────────────────────────────────────

def _resolve_tz(tz_hint: str) -> str:
    """Return an IANA timezone string from a hint like 'paris', 'Europe/Paris', 'CET'."""
    if not tz_hint:
        return "UTC"
    candidate = _TZ_ALIASES.get(tz_hint.lower(), tz_hint)
    try:
        ZoneInfo(candidate)
        return candidate
    except (ZoneInfoNotFoundError, KeyError):
        return "UTC"


def _parse_time(text: str, tz_hint: str = "") -> tuple[float, str]:
    """Parse natural language time expression. Returns (unix_timestamp, tz_name).

    Raises ValueError if nothing recognisable is found.
    """
    text_lower = text.lower()

    # Extract timezone from text if not provided separately
    tz_name = _resolve_tz(tz_hint) if tz_hint else "UTC"
    for alias, iana in _TZ_ALIASES.items():
        if alias in text_lower:
            tz_name = iana
            break

    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz=tz)

    # "in N minutes" / "in N hours"
    m = re.search(r"in\s+(\d+)\s+(minute|hour)", text_lower)
    if m:
        n = int(m.group(1))
        delta = timedelta(minutes=n) if "minute" in m.group(2) else timedelta(hours=n)
        return (datetime.now() + delta).timestamp(), tz_name

    # "tomorrow at HH:MM"
    tomorrow = "tomorrow" in text_lower

    # "at HH:MM" or "HH:MM"
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if tomorrow:
            target += timedelta(days=1)
        return target.timestamp(), tz_name

    raise ValueError("no recognisable time pattern found (expected HH:MM or 'in N minutes/hours')")


def _extract_message(raw: str) -> str:
    """Strip the scheduling preamble and return the reminder content."""
    # Remove "remind me at HH:MM [tz] to" prefix
    cleaned = re.sub(
        r"remind\s+me\s+(at\s+[\d:]+\s*\w*\s*)?(to\s+)?",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned or raw
