"""Tests for the kernel scheduler."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from macroa.kernel.scheduler import Scheduler, _next_cron, _parse_next_run

# ------------------------------------------------------------------ parse helpers

def test_once_no_prior_run():
    future = time.time() + 3600
    result = _parse_next_run(f"once:{future}", now=time.time(), last_run_at=None)
    assert abs(result - future) < 1


def test_once_already_ran():
    future = time.time() + 3600
    result = _parse_next_run(f"once:{future}", now=time.time(), last_run_at=time.time())
    assert result is None


def test_every_first_run():
    now = time.time()
    result = _parse_next_run("every:60", now=now, last_run_at=None)
    assert abs(result - (now + 60)) < 1


def test_every_after_run():
    now = time.time()
    last = now - 10
    result = _parse_next_run("every:60", now=now, last_run_at=last)
    assert abs(result - (last + 60)) < 1


def test_daily_future():
    now = time.time()
    result = _parse_next_run("daily:23:59", now=now, last_run_at=None)
    assert result > now


def test_cron_next():
    now = time.time()
    result = _next_cron("* * * * *", now)  # every minute
    assert result > now
    assert result - now <= 61


def test_cron_specific_hour():
    now = time.time()
    result = _next_cron("0 3 * * *", now)  # 03:00 every day
    assert result > now


def test_unknown_spec_raises():
    with pytest.raises(ValueError):
        _parse_next_run("invalid:spec", now=time.time(), last_run_at=None)


# ------------------------------------------------------------------ Scheduler CRUD

def _sched(tmp_path: Path) -> Scheduler:
    calls = []
    def noop(cmd, session_id): calls.append(cmd)
    sched = Scheduler(db_path=tmp_path / "sched.db", run_fn=noop, poll_interval=60)
    sched._calls = calls  # attach for inspection
    return sched


def test_add_and_list(tmp_path):
    s = _sched(tmp_path)
    task = s.add("ping", "!echo hi", "every:300", session_id="sess-1")
    assert task.label == "ping"
    tasks = s.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].task_id == task.task_id


def test_delete(tmp_path):
    s = _sched(tmp_path)
    task = s.add("t", "cmd", "every:60", session_id="s")
    deleted = s.delete(task.task_id)
    assert deleted is True
    assert s.list_tasks() == []


def test_delete_nonexistent(tmp_path):
    s = _sched(tmp_path)
    result = s.delete("no-such-id")
    assert result is False


def test_enable_disable(tmp_path):
    s = _sched(tmp_path)
    task = s.add("t", "cmd", "every:60", session_id="s")
    assert s.enable(task.task_id, False) is True
    assert s.list_tasks(include_disabled=False) == []
    assert len(s.list_tasks(include_disabled=True)) == 1


def test_once_task_removed_after_fire(tmp_path):
    fired = []
    def run_fn(cmd, sid): fired.append(cmd)
    s = Scheduler(db_path=tmp_path / "s.db", run_fn=run_fn, poll_interval=60)
    # Schedule in the past so it fires immediately
    past = time.time() - 1
    s.add("once-task", "!echo once", f"once:{past}", session_id="s")
    s._tick()
    assert fired == ["!echo once"]
    assert s.list_tasks() == []  # removed after one-shot


def test_recurring_task_rescheduled(tmp_path):
    fired = []
    def run_fn(cmd, sid): fired.append(cmd)
    s = Scheduler(db_path=tmp_path / "s.db", run_fn=run_fn, poll_interval=60)
    past = time.time() - 1
    # Directly insert a task with next_run_at in the past
    task = s.add("rep", "!echo repeat", "every:300", session_id="s")
    # Force next_run_at to be in the past
    with s._lock:
        s._conn.execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE task_id = ?",
            (past, task.task_id),
        )
        s._conn.commit()
    s._tick()
    assert len(fired) == 1
    # Task should still exist with updated next_run_at
    tasks = s.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].next_run_at > time.time()
    assert tasks[0].run_count == 1


# ------------------------------------------------------------------ daemon thread

def test_scheduler_fires_via_thread(tmp_path):
    fired = []
    event = threading.Event()

    def run_fn(cmd, sid):
        fired.append(cmd)
        event.set()

    s = Scheduler(db_path=tmp_path / "s.db", run_fn=run_fn, poll_interval=0.1)
    past = time.time() - 1
    s.add("bg", "!echo bg", f"once:{past}", session_id="s")
    s.start()
    event.wait(timeout=3)
    s.stop()
    assert "!echo bg" in fired


def test_scheduler_isolates_run_fn_exception(tmp_path):
    def boom(cmd, sid): raise RuntimeError("explode")
    s = Scheduler(db_path=tmp_path / "s.db", run_fn=boom, poll_interval=0.1)
    past = time.time() - 1
    s.add("crasher", "cmd", f"once:{past}", session_id="s")
    s.start()
    time.sleep(0.5)
    assert s.running  # thread still alive despite crash
    s.stop()
