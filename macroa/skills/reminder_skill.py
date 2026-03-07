"""Reminder skill — full CRUD for scheduled reminders.

Actions (auto-detected from raw input or explicit parameter):
  add    — schedule a new one-shot reminder (default)
  list   — show all pending reminders
  delete — cancel a reminder by matching its time or label fragment

Natural language time formats supported:
  "at HH:MM"                     — today or tomorrow if time has passed
  "at HH:MM <timezone/city>"     — explicit TZ (paris, london, UTC, ...)
  "in N minutes / hours"         — relative offset
  "tomorrow at HH:MM"            — next day
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from macroa.kernel.clock import get_user_timezone
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
        "Schedule, list, or cancel reminders. "
        "Use for: 'remind me at HH:MM [timezone] to <message>', "
        "'remind me in N minutes to <message>', "
        "'list my reminders', 'cancel/remove/delete the HH:MM reminder'. "
        "Understands city names as timezones (paris, london, berlin, etc.)."
    ),
    triggers=[
        "remind me", "set a reminder", "alert me at", "wake me at",
        "reminder at", "remind me at", "remind me in",
        "list reminders", "show reminders", "my reminders",
        "cancel reminder", "remove reminder", "delete reminder",
        "cancel the", "remove the", "delete the",
    ],
    model_tier=None,
    deterministic=True,
)

# City/abbreviation → IANA timezone
_TZ_ALIASES: dict[str, str] = {
    "paris": "Europe/Paris",
    "london": "Europe/London",
    "berlin": "Europe/Berlin",
    "warsaw": "Europe/Warsaw",
    "amsterdam": "Europe/Amsterdam",
    "rome": "Europe/Rome",
    "madrid": "Europe/Madrid",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "la": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "toronto": "America/Toronto",
    "tokyo": "Asia/Tokyo",
    "seoul": "Asia/Seoul",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "sydney": "Australia/Sydney",
    "dubai": "Asia/Dubai",
    "mumbai": "Asia/Kolkata",
    "utc": "UTC",
    "gmt": "GMT",
    "est": "America/New_York",
    "edt": "America/New_York",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "cst": "America/Chicago",
    "cet": "Europe/Paris",
    "cest": "Europe/Paris",
    "ist": "Asia/Kolkata",
    "jst": "Asia/Tokyo",
    "aest": "Australia/Sydney",
}

_REMOVE_SIGNALS = frozenset(["remove", "cancel", "delete"])
_LIST_SIGNALS = frozenset(["list", "show", "what", "all"])


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    raw = intent.raw.strip()
    action = _detect_action(raw, intent.parameters)

    if action == "list":
        return _list_reminders(intent)
    elif action == "delete":
        return _delete_reminder(raw, intent)
    else:
        return _add_reminder(raw, intent, drivers)


# ── action detection ──────────────────────────────────────────────────────────

def _detect_action(raw: str, params: dict) -> str:
    explicit = params.get("action", "").lower()
    if explicit in ("list", "delete", "cancel", "remove", "add"):
        return "delete" if explicit in ("delete", "cancel", "remove") else explicit

    lower = raw.lower()
    has_remove = any(s in lower for s in _REMOVE_SIGNALS)
    has_reminder_ref = "reminder" in lower or re.search(r"\d{1,2}:\d{2}", lower) is not None

    # "remove the 03:08 reminder" — contains remove signal AND a time/reminder ref
    # but NOT "remind me" (which would be an add)
    if has_remove and has_reminder_ref and "remind me" not in lower:
        return "delete"

    if any(s in lower for s in _LIST_SIGNALS) and "reminder" in lower and "remind me" not in lower:
        return "list"

    return "add"


# ── add ───────────────────────────────────────────────────────────────────────

def _add_reminder(raw: str, intent: Intent, drivers: DriverBundle) -> SkillResult:
    time_str = intent.parameters.get("time", raw)
    message = intent.parameters.get("message", "").strip() or _extract_message(raw)
    tz_param = intent.parameters.get("timezone", "").strip()

    # Use stored user timezone as default when none specified in the message
    user_tz = get_user_timezone(drivers.memory)

    try:
        target_ts, tz_used = _parse_time(time_str, tz_param or user_tz)
    except ValueError as exc:
        return SkillResult(
            output="",
            success=False,
            error=f"Could not parse time from {time_str!r}: {exc}",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )

    if target_ts <= time.time():
        target_ts += 86_400  # push to tomorrow if already past

    label = f"reminder: {message[:60]}"
    spec = f"once:{int(target_ts)}"

    try:
        import macroa.kernel as kernel
        task = kernel.schedule_add(label=label, command=message, schedule=spec)
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


# ── list ──────────────────────────────────────────────────────────────────────

def _list_reminders(intent: Intent) -> SkillResult:
    try:
        import macroa.kernel as kernel
        tasks = kernel.schedule_list()
    except Exception as exc:
        return SkillResult(
            output="", success=False,
            error=f"Could not fetch reminders: {exc}",
            turn_id=intent.turn_id, model_tier=intent.model_tier,
        )

    reminders = [t for t in tasks if t.label.startswith("reminder:")]
    if not reminders:
        return SkillResult(
            output="No reminders scheduled.",
            success=True,
            turn_id=intent.turn_id, model_tier=intent.model_tier,
        )

    lines = []
    for t in reminders:
        when = datetime.fromtimestamp(t.next_run_at).strftime("%H:%M on %a %d %b")
        lines.append(f"• [{t.task_id[:6]}] {when} — {t.command}")
    return SkillResult(
        output="\n".join(lines), success=True,
        turn_id=intent.turn_id, model_tier=intent.model_tier,
        metadata={"count": len(reminders)},
    )


# ── delete ────────────────────────────────────────────────────────────────────

def _delete_reminder(raw: str, intent: Intent) -> SkillResult:
    try:
        import macroa.kernel as kernel
        tasks = kernel.schedule_list(include_disabled=True)
    except Exception as exc:
        return SkillResult(
            output="", success=False,
            error=f"Could not fetch reminders: {exc}",
            turn_id=intent.turn_id, model_tier=intent.model_tier,
        )

    reminders = [t for t in tasks if t.label.startswith("reminder:")]
    if not reminders:
        return SkillResult(
            output="No reminders to cancel.", success=True,
            turn_id=intent.turn_id, model_tier=intent.model_tier,
        )

    match = None
    raw_lower = raw.lower()

    # Match by HH:MM in raw input
    time_m = re.search(r"\b(\d{1,2}:\d{2})\b", raw)
    if time_m:
        query_time = time_m.group(1)
        for t in reminders:
            when = datetime.fromtimestamp(t.next_run_at).strftime("%H:%M")
            if when == query_time:
                match = t
                break

    # Fall back to command/label substring
    if not match:
        for t in reminders:
            frag = t.command.lower()
            if frag in raw_lower or t.label.lower().replace("reminder: ", "") in raw_lower:
                match = t
                break

    if not match:
        times = ", ".join(
            datetime.fromtimestamp(t.next_run_at).strftime("%H:%M") for t in reminders
        )
        return SkillResult(
            output=f"Couldn't find a matching reminder. Pending: {times}",
            success=True,
            turn_id=intent.turn_id, model_tier=intent.model_tier,
        )

    try:
        import macroa.kernel as kernel
        kernel.schedule_delete(match.task_id)
    except Exception as exc:
        return SkillResult(
            output="", success=False,
            error=f"Failed to delete reminder: {exc}",
            turn_id=intent.turn_id, model_tier=intent.model_tier,
        )

    when = datetime.fromtimestamp(match.next_run_at).strftime("%H:%M on %a %d %b")
    return SkillResult(
        output=f"Cancelled reminder at {when}: {match.command}",
        success=True,
        turn_id=intent.turn_id, model_tier=intent.model_tier,
        metadata={"task_id": match.task_id},
    )


# ── time parsing ──────────────────────────────────────────────────────────────

def _resolve_tz(hint: str) -> str:
    if not hint:
        return "UTC"
    candidate = _TZ_ALIASES.get(hint.lower().strip(), hint.strip())
    try:
        ZoneInfo(candidate)
        return candidate
    except (ZoneInfoNotFoundError, KeyError):
        return "UTC"


def _parse_time(text: str, default_tz: str = "UTC") -> tuple[float, str]:
    """Parse natural language time. Returns (unix_timestamp, tz_name).

    Raises ValueError if no recognisable pattern found.
    """
    text_lower = text.lower()

    # Detect timezone mention in text (city or abbreviation)
    tz_name = default_tz
    for alias, iana in _TZ_ALIASES.items():
        if alias in text_lower:
            tz_name = iana
            break
    tz_name = _resolve_tz(tz_name)
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz=tz)

    # "in N minutes/hours"
    m = re.search(r"\bin\s+(\d+)\s+(minute|hour)", text_lower)
    if m:
        n = int(m.group(1))
        delta = timedelta(minutes=n) if "minute" in m.group(2) else timedelta(hours=n)
        return (datetime.now() + delta).timestamp(), tz_name

    tomorrow = "tomorrow" in text_lower

    # "HH:MM"
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if tomorrow:
            target += timedelta(days=1)
        return target.timestamp(), tz_name

    raise ValueError("no HH:MM or 'in N minutes/hours' found")


def _extract_message(raw: str) -> str:
    """Strip scheduling preamble, return only the reminder content."""
    # "remind me at 03:09 paris time to jugg" → "jugg"
    cleaned = re.sub(
        r"^(?:please\s+)?remind\s+me\s+"
        r"(?:(?:at\s+\d{1,2}:\d{2}(?:\s+\w+(?:\s+time)?)?|in\s+\d+\s+(?:minute|hour)s?)\s+)?"
        r"(?:to\s+)?",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    # Drop trailing time reference: "jugg at 03:09 paris time" → "jugg"
    cleaned = re.sub(
        r"\s+at\s+\d{1,2}:\d{2}(?:\s+\w+(?:\s+time)?)?\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned or raw
