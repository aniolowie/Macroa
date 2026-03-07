"""Tests for v3 features: clock, reminder CRUD, REMINDER_FIRED notification pipeline."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── clock.get_user_timezone ────────────────────────────────────────────────────


class TestGetUserTimezone:
    def test_returns_stored_memory_tz(self):
        from macroa.kernel.clock import get_user_timezone

        memory = MagicMock()
        memory.get.return_value = "Europe/Paris"
        assert get_user_timezone(memory) == "Europe/Paris"
        memory.get.assert_called_once_with("user", "timezone")

    def test_ignores_invalid_stored_tz(self):
        from macroa.kernel.clock import get_user_timezone

        memory = MagicMock()
        memory.get.return_value = "Not/A/RealZone"
        tz = get_user_timezone(memory)
        assert tz != "Not/A/RealZone"

    def test_none_memory_returns_valid_string(self):
        from macroa.kernel.clock import get_user_timezone

        tz = get_user_timezone(None)
        assert isinstance(tz, str) and len(tz) > 0

    def test_fallback_to_utc_when_no_system_link(self):
        from macroa.kernel.clock import get_user_timezone

        memory = MagicMock()
        memory.get.return_value = None
        with patch("macroa.kernel.clock.os.path.islink", return_value=False):
            tz = get_user_timezone(memory)
        assert tz == "UTC"

    def test_memory_none_returns_false_graceful(self):
        from macroa.kernel.clock import get_user_timezone

        with patch("macroa.kernel.clock.os.path.islink", return_value=False):
            tz = get_user_timezone(None)
        assert tz == "UTC"


# ── clock.now_context ─────────────────────────────────────────────────────────


class TestNowContext:
    def test_starts_with_current_time_label(self):
        from macroa.kernel.clock import now_context

        assert now_context(None).startswith("Current time:")

    def test_contains_year(self):
        from macroa.kernel.clock import now_context

        result = now_context(None)
        assert str(datetime.now().year) in result

    def test_contains_hhmm(self):
        import re
        from macroa.kernel.clock import now_context

        assert re.search(r"\d{2}:\d{2}", now_context(None))

    def test_contains_requested_timezone(self):
        from macroa.kernel.clock import now_context

        memory = MagicMock()
        memory.get.return_value = "America/New_York"
        assert "America/New_York" in now_context(memory)

    def test_utc_fallback_in_output(self):
        from macroa.kernel.clock import now_context

        with patch("macroa.kernel.clock.os.path.islink", return_value=False):
            result = now_context(None)
        assert "UTC" in result


# ── reminder_skill._detect_action ─────────────────────────────────────────────


class TestDetectAction:
    @pytest.mark.parametrize("raw,expected", [
        ("remind me at 03:00 to do the thing", "add"),
        ("remind me in 10 minutes to take a pill", "add"),
        ("set a reminder for tomorrow", "add"),
        ("remind me to call mom", "add"),
        ("remove the 03:08 reminder", "delete"),
        ("cancel the reminder", "delete"),
        ("delete my 14:00 reminder", "delete"),
        ("list my reminders", "list"),
        ("show all reminders", "list"),
        ("what reminders do i have", "list"),
    ])
    def test_detect_action_from_text(self, raw: str, expected: str):
        from macroa.skills.reminder_skill import _detect_action

        assert _detect_action(raw, {}) == expected

    def test_explicit_param_list_overrides(self):
        from macroa.skills.reminder_skill import _detect_action

        assert _detect_action("remind me to do stuff", {"action": "list"}) == "list"

    def test_explicit_param_delete_overrides(self):
        from macroa.skills.reminder_skill import _detect_action

        assert _detect_action("remind me to do stuff", {"action": "delete"}) == "delete"


# ── reminder_skill._parse_time ────────────────────────────────────────────────


class TestParseTime:
    def test_in_n_minutes(self):
        from macroa.skills.reminder_skill import _parse_time

        before = time.time()
        ts, tz = _parse_time("in 5 minutes", "UTC")
        assert before + 295 < ts < time.time() + 305
        assert tz == "UTC"

    def test_in_n_hours(self):
        from macroa.skills.reminder_skill import _parse_time

        before = time.time()
        ts, tz = _parse_time("in 2 hours", "UTC")
        assert before + 7190 < ts < time.time() + 7210

    def test_at_hhmm_gives_correct_minute(self):
        from macroa.skills.reminder_skill import _parse_time

        ts, tz = _parse_time("at 23:59", "UTC")
        dt = datetime.fromtimestamp(ts)
        assert dt.minute == 59
        assert tz == "UTC"

    def test_timezone_extracted_from_text(self):
        from macroa.skills.reminder_skill import _parse_time

        ts, tz = _parse_time("remind me at 15:00 paris time", "UTC")
        assert tz == "Europe/Paris"

    def test_tomorrow_is_24h_later(self):
        from macroa.skills.reminder_skill import _parse_time

        ts_today, _ = _parse_time("at 12:00", "UTC")
        ts_tomorrow, _ = _parse_time("tomorrow at 12:00", "UTC")
        assert 86390 < ts_tomorrow - ts_today < 86410

    def test_invalid_raises_value_error(self):
        from macroa.skills.reminder_skill import _parse_time

        with pytest.raises(ValueError):
            _parse_time("no time here at all", "UTC")

    def test_default_tz_used_when_none_in_text(self):
        from macroa.skills.reminder_skill import _parse_time

        _, tz = _parse_time("at 10:00", "Asia/Tokyo")
        assert tz == "Asia/Tokyo"


# ── reminder_skill._extract_message ───────────────────────────────────────────


class TestExtractMessage:
    @pytest.mark.parametrize("raw,expected", [
        ("remind me at 03:09 paris time to jugg", "jugg"),
        ("remind me in 10 minutes to take a pill", "take a pill"),
        ("remind me to call mom", "call mom"),
        ("please remind me at 08:00 to exercise", "exercise"),
    ])
    def test_strips_preamble(self, raw: str, expected: str):
        from macroa.skills.reminder_skill import _extract_message

        assert _extract_message(raw) == expected


# ── reminder_skill._resolve_tz ────────────────────────────────────────────────


class TestResolveTz:
    def test_city_alias_paris(self):
        from macroa.skills.reminder_skill import _resolve_tz

        assert _resolve_tz("paris") == "Europe/Paris"

    def test_city_alias_nyc(self):
        from macroa.skills.reminder_skill import _resolve_tz

        assert _resolve_tz("nyc") == "America/New_York"

    def test_direct_iana_name(self):
        from macroa.skills.reminder_skill import _resolve_tz

        assert _resolve_tz("America/Chicago") == "America/Chicago"

    def test_invalid_falls_back_to_utc(self):
        from macroa.skills.reminder_skill import _resolve_tz

        assert _resolve_tz("Definitely/NotReal") == "UTC"

    def test_empty_string_returns_utc(self):
        from macroa.skills.reminder_skill import _resolve_tz

        assert _resolve_tz("") == "UTC"


# ── REMINDER_FIRED event emission ─────────────────────────────────────────────


class TestReminderFiredEvent:
    def test_fire_emits_event_for_reminder_task(self, tmp_path: Path):
        from macroa.kernel.events import Events, bus
        from macroa.kernel.scheduler import Scheduler, ScheduledTask

        received: list = []

        def handler(event):
            received.append(event)

        bus.subscribe(Events.REMINDER_FIRED, handler)
        try:
            scheduler = Scheduler(
                db_path=tmp_path / "sched.db",
                run_fn=lambda cmd, sid: None,
            )
            task = ScheduledTask(
                task_id="abc-123",
                label="reminder: take medicine",
                command="take medicine",
                schedule="once:9999999999",
                session_id="test-session",
            )
            scheduler._fire(task, time.time())
        finally:
            bus.unsubscribe(Events.REMINDER_FIRED, handler)

        assert len(received) == 1
        evt = received[0]
        assert evt.event_type == Events.REMINDER_FIRED
        assert evt.source == "scheduler"
        assert evt.payload["task_id"] == "abc-123"
        assert evt.payload["message"] == "take medicine"
        assert evt.session_id == "test-session"

    def test_fire_does_not_emit_for_non_reminder_task(self, tmp_path: Path):
        from macroa.kernel.events import Events, bus
        from macroa.kernel.scheduler import Scheduler, ScheduledTask

        received: list = []

        def handler(event):
            received.append(event)

        bus.subscribe(Events.REMINDER_FIRED, handler)
        try:
            scheduler = Scheduler(
                db_path=tmp_path / "sched2.db",
                run_fn=lambda cmd, sid: None,
            )
            task = ScheduledTask(
                task_id="xyz-999",
                label="heartbeat: daily ping",
                command="ping",
                schedule="once:9999999999",
                session_id="test-session",
            )
            scheduler._fire(task, time.time())
        finally:
            bus.unsubscribe(Events.REMINDER_FIRED, handler)

        assert len(received) == 0

    def test_payload_contains_fired_at(self, tmp_path: Path):
        from macroa.kernel.events import Events, bus
        from macroa.kernel.scheduler import Scheduler, ScheduledTask

        received: list = []
        bus.subscribe(Events.REMINDER_FIRED, lambda e: received.append(e))

        now = time.time()
        try:
            scheduler = Scheduler(
                db_path=tmp_path / "sched3.db",
                run_fn=lambda cmd, sid: None,
            )
            task = ScheduledTask(
                task_id="t1",
                label="reminder: check logs",
                command="check logs",
                schedule="once:9999999999",
                session_id="s1",
            )
            scheduler._fire(task, now)
        finally:
            bus.unsubscribe(Events.REMINDER_FIRED, received.append)

        assert received[0].payload["fired_at"] == now

    def test_run_fn_exception_does_not_suppress_event(self, tmp_path: Path):
        """Even if the run_fn fails, the REMINDER_FIRED event should still emit."""
        from macroa.kernel.events import Events, bus
        from macroa.kernel.scheduler import Scheduler, ScheduledTask

        received: list = []

        def handler(event):
            received.append(event)

        def failing_run(cmd, sid):
            raise RuntimeError("kernel down")

        bus.subscribe(Events.REMINDER_FIRED, handler)
        try:
            scheduler = Scheduler(
                db_path=tmp_path / "sched4.db",
                run_fn=failing_run,
            )
            task = ScheduledTask(
                task_id="t2",
                label="reminder: even if kernel fails",
                command="msg",
                schedule="once:9999999999",
                session_id="s2",
            )
            scheduler._fire(task, time.time())
        finally:
            bus.unsubscribe(Events.REMINDER_FIRED, handler)

        assert len(received) == 1


# ── REPL notification handler ─────────────────────────────────────────────────


class TestReplNotificationHandler:
    def test_on_reminder_fired_prints_banner(self):
        from macroa.cli.main import _on_reminder_fired
        from macroa.kernel.events import Event, Events

        evt = Event(
            event_type=Events.REMINDER_FIRED,
            source="scheduler",
            payload={"message": "call dentist", "task_id": "x", "fired_at": time.time()},
        )

        with patch("macroa.cli.main.console") as mock_console:
            _on_reminder_fired(evt)

        mock_console.print.assert_called_once()
        printed_text = mock_console.print.call_args[0][0]
        assert "call dentist" in printed_text
        assert "Reminder" in printed_text

    def test_on_reminder_fired_missing_message_does_not_crash(self):
        from macroa.cli.main import _on_reminder_fired
        from macroa.kernel.events import Event, Events

        evt = Event(
            event_type=Events.REMINDER_FIRED,
            source="scheduler",
            payload={},  # no message key
        )
        with patch("macroa.cli.main.console"):
            _on_reminder_fired(evt)  # must not raise
