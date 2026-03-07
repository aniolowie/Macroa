"""Tests for daemon control: PID file, start/stop, status, banner integration."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Helpers ───────────────────────────────────────────────────────────────────


def _patch_macroa_dir(tmp_path: Path):
    """Patch MACROA_DIR so all daemon paths go under tmp_path."""
    return patch("macroa.kernel.daemon._macroa_dir", return_value=tmp_path)


# ── is_running ────────────────────────────────────────────────────────────────


class TestIsRunning:
    def test_no_pid_file_returns_false(self, tmp_path: Path):
        from macroa.kernel.daemon import is_running

        with _patch_macroa_dir(tmp_path):
            assert is_running() is False

    def test_stale_pid_file_returns_false(self, tmp_path: Path):
        from macroa.kernel.daemon import is_running

        # Write a PID that almost certainly does not exist
        (tmp_path / "daemon.pid").write_text("99999999")
        with _patch_macroa_dir(tmp_path):
            assert is_running() is False

    def test_stale_pid_file_is_cleaned_up(self, tmp_path: Path):
        from macroa.kernel.daemon import is_running

        pf = tmp_path / "daemon.pid"
        pf.write_text("99999999")
        with _patch_macroa_dir(tmp_path):
            is_running()
        assert not pf.exists()

    def test_live_pid_returns_true(self, tmp_path: Path):
        from macroa.kernel.daemon import is_running

        # Our own PID is definitely alive
        (tmp_path / "daemon.pid").write_text(str(os.getpid()))
        with _patch_macroa_dir(tmp_path):
            assert is_running() is True


# ── read_status ───────────────────────────────────────────────────────────────


class TestReadStatus:
    def test_missing_status_returns_empty_dict(self, tmp_path: Path):
        from macroa.kernel.daemon import read_status

        with _patch_macroa_dir(tmp_path):
            assert read_status() == {}

    def test_reads_valid_json(self, tmp_path: Path):
        from macroa.kernel.daemon import read_status

        (tmp_path / "daemon_status.json").write_text('{"pid": 1, "web_enabled": true}')
        with _patch_macroa_dir(tmp_path):
            st = read_status()
        assert st["pid"] == 1
        assert st["web_enabled"] is True

    def test_corrupt_json_returns_empty_dict(self, tmp_path: Path):
        from macroa.kernel.daemon import read_status

        (tmp_path / "daemon_status.json").write_text("{not valid json")
        with _patch_macroa_dir(tmp_path):
            assert read_status() == {}


# ── stop ──────────────────────────────────────────────────────────────────────


class TestStop:
    def test_stop_no_daemon_returns_false(self, tmp_path: Path):
        from macroa.kernel.daemon import stop

        with _patch_macroa_dir(tmp_path):
            assert stop() is False

    def test_stop_stale_pid_returns_false(self, tmp_path: Path):
        from macroa.kernel.daemon import stop

        (tmp_path / "daemon.pid").write_text("99999999")
        with _patch_macroa_dir(tmp_path):
            result = stop()
        assert result is False

    def test_stop_sends_sigterm_and_cleans_up(self, tmp_path: Path):
        from macroa.kernel.daemon import stop

        # Write own PID — sending SIGTERM to self would kill the test, so mock os.kill
        (tmp_path / "daemon.pid").write_text("12345")
        (tmp_path / "daemon_status.json").write_text("{}")

        kill_calls: list = []
        sigterm_sent = False

        def fake_kill(pid, sig):
            nonlocal sigterm_sent
            kill_calls.append((pid, sig))
            if sig == signal.SIGTERM:
                sigterm_sent = True
                return  # SIGTERM succeeds
            if sig == 0:
                # After SIGTERM was sent, process is gone
                if sigterm_sent:
                    raise ProcessLookupError
                return  # before SIGTERM: process exists

        with _patch_macroa_dir(tmp_path), patch("os.kill", side_effect=fake_kill):
            result = stop()

        assert result is True
        # SIGTERM was sent
        assert any(sig == signal.SIGTERM for _, sig in kill_calls)
        # PID file cleaned up
        assert not (tmp_path / "daemon.pid").exists()


# ── start ─────────────────────────────────────────────────────────────────────


class TestStart:
    def test_start_raises_if_already_running(self, tmp_path: Path):
        from macroa.kernel.daemon import start

        (tmp_path / "daemon.pid").write_text(str(os.getpid()))
        with _patch_macroa_dir(tmp_path):
            try:
                start()
                assert False, "Expected RuntimeError"
            except RuntimeError as exc:
                assert "already running" in str(exc)

    def test_start_spawns_subprocess(self, tmp_path: Path):
        from macroa.kernel.daemon import start

        fake_proc = MagicMock()
        fake_proc.pid = 42424
        fake_proc.poll.return_value = None  # still alive

        def fake_popen(args, **kwargs):
            # Write PID file as the daemon would
            (tmp_path / "daemon.pid").write_text("42424")
            return fake_proc

        with _patch_macroa_dir(tmp_path), \
             patch("subprocess.Popen", side_effect=fake_popen), \
             patch("macroa.kernel.daemon.is_running", side_effect=[False, True]):
            pid = start(port=8001, web=False)

        assert pid == 42424


# ── daemon banner ─────────────────────────────────────────────────────────────


class TestDaemonBanner:
    def test_offline_shows_message(self, tmp_path: Path):
        from macroa.cli.renderer import _get_daemon_status

        with _patch_macroa_dir(tmp_path):
            result = _get_daemon_status()
        assert "offline" in result

    def test_running_shows_status(self, tmp_path: Path):
        from macroa.cli.renderer import _get_daemon_status

        (tmp_path / "daemon.pid").write_text(str(os.getpid()))
        (tmp_path / "daemon_status.json").write_text(
            '{"pid": ' + str(os.getpid()) + ', "scheduler_tasks": 3, "web_port": 8000}'
        )
        with _patch_macroa_dir(tmp_path):
            result = _get_daemon_status()
        assert "running" in result
        assert "tasks: 3" in result
        assert ":8000" in result

    def test_running_no_web(self, tmp_path: Path):
        from macroa.cli.renderer import _get_daemon_status

        (tmp_path / "daemon.pid").write_text(str(os.getpid()))
        (tmp_path / "daemon_status.json").write_text(
            '{"pid": ' + str(os.getpid()) + ', "scheduler_tasks": 0, "web_port": null}'
        )
        with _patch_macroa_dir(tmp_path):
            result = _get_daemon_status()
        assert "running" in result
        assert ":8000" not in result
