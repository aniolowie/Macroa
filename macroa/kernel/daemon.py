"""Macroa daemon — persistent background process.

Keeps the scheduler, watchdog, and optional HTTP API alive when no REPL
is open. Spawned as a detached subprocess; communicates via a PID file
and a status JSON file.

Usage (via CLI):
    macroa daemon start [--port 8000] [--no-web]
    macroa daemon stop
    macroa daemon status

Internals:
    PID file   : ~/.macroa/daemon.pid
    Status JSON: ~/.macroa/daemon_status.json
    Log file   : ~/.macroa/logs/daemon.log
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Path helpers ──────────────────────────────────────────────────────────────

def _macroa_dir() -> Path:
    from macroa.vfs.layout import MACROA_DIR
    return MACROA_DIR


def pid_file() -> Path:
    return _macroa_dir() / "daemon.pid"


def status_file() -> Path:
    return _macroa_dir() / "daemon_status.json"


def log_file() -> Path:
    p = _macroa_dir() / "logs" / "daemon.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── Public control API ────────────────────────────────────────────────────────

def is_running() -> bool:
    """Return True if a daemon process is alive."""
    pf = pid_file()
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)  # signal 0 = existence check only
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        # Stale PID file — clean up
        try:
            pf.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort cleanup — if unlink fails, just proceed
        return False


def read_status() -> dict:
    """Return the last written status dict, or an empty dict if unavailable."""
    sf = status_file()
    if not sf.exists():
        return {}
    try:
        return json.loads(sf.read_text())
    except Exception:
        return {}


def start(port: int = 8000, web: bool = True) -> int:
    """Spawn the daemon process and return its PID.

    Raises RuntimeError if a daemon is already running.
    """
    if is_running():
        pid = int(pid_file().read_text().strip())
        raise RuntimeError(f"Daemon already running (PID {pid}).")

    log = log_file()
    args = [sys.executable, "-m", "macroa.kernel.daemon", "--port", str(port)]
    if not web:
        args.append("--no-web")

    import subprocess
    with open(log, "a") as log_fh:
        proc = subprocess.Popen(
            args,
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from terminal
        )

    # Wait up to 3 s for the PID file to appear (confirms daemon started)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if pid_file().exists():
            try:
                written_pid = int(pid_file().read_text().strip())
                if written_pid == proc.pid or is_running():
                    return proc.pid
            except ValueError:
                pass  # PID file written mid-write; retry on next iteration
        time.sleep(0.1)

    # PID file never appeared — check if process is still alive
    if proc.poll() is None:
        return proc.pid
    raise RuntimeError(
        f"Daemon process exited immediately. Check log: {log}"
    )


def stop() -> bool:
    """Send SIGTERM to the daemon. Returns True if a signal was sent."""
    pf = pid_file()
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5 s for graceful shutdown
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
        pf.unlink(missing_ok=True)
        status_file().unlink(missing_ok=True)
        return True
    except (ValueError, ProcessLookupError):
        pf.unlink(missing_ok=True)
        return False


# ── Daemon entry point ────────────────────────────────────────────────────────

def _daemon_main(port: int = 8000, web: bool = True) -> None:
    """Run the daemon: init kernel, start web API, write heartbeats."""
    import threading

    # Redirect logging to the log file (already done via subprocess stdout/stderr)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logger.info("Macroa daemon starting (PID %d, port=%d, web=%s)", os.getpid(), port, web)

    # Write PID file immediately
    pf = pid_file()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(os.getpid()))

    # Initialise kernel (loads scheduler, watchdog, memory, etc.)
    import macroa.kernel as kernel
    kernel.get_session_id()  # triggers lazy init

    started_at = time.time()
    stop_event = threading.Event()

    def _on_sigterm(signum, frame):  # noqa: ARG001
        logger.info("Daemon received SIGTERM — shutting down")
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    # Start web API in a daemon thread
    web_thread: threading.Thread | None = None
    if web:
        try:
            import uvicorn

            def _run_web():
                uvicorn.run(
                    "macroa.web.app:app",
                    host="127.0.0.1",
                    port=port,
                    log_level="warning",
                )

            web_thread = threading.Thread(target=_run_web, daemon=True, name="macroa-web")
            web_thread.start()
            logger.info("Web API starting on http://127.0.0.1:%d", port)
        except ImportError:
            logger.warning("uvicorn not installed — web API disabled")
            web = False

    # Heartbeat loop — writes status JSON every 30 s
    sf = status_file()
    while not stop_event.is_set():
        try:
            tasks = kernel.schedule_list(include_disabled=False)
            sf.write_text(json.dumps({
                "pid": os.getpid(),
                "started_at": started_at,
                "web_port": port if web else None,
                "web_enabled": web,
                "scheduler_tasks": len(tasks),
                "updated_at": time.time(),
            }, indent=2))
        except Exception as exc:
            logger.debug("Status write failed: %s", exc)

        stop_event.wait(timeout=30)

    # Graceful teardown
    logger.info("Daemon shutting down")
    try:
        kernel.shutdown()
    except Exception:
        pass  # shutdown errors are non-fatal during daemon teardown
    pf.unlink(missing_ok=True)
    sf.unlink(missing_ok=True)
    logger.info("Daemon stopped")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Macroa daemon process")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-web", action="store_true")
    args = parser.parse_args()
    _daemon_main(port=args.port, web=not args.no_web)
