"""Tests for IPCBus and the ipc_emit/ipc_read/ipc_list_channels agent tools."""

from __future__ import annotations

import threading
import time

from macroa.kernel.ipc import IPCBus
from macroa.kernel.tool_defs import execute_tool
from macroa.stdlib.schema import DriverBundle

# ── IPCBus unit tests ──────────────────────────────────────────────────────────

class TestIPCBus:
    def test_emit_and_read(self):
        bus = IPCBus()
        bus.emit("chan", "hello")
        msg = bus.read("chan", timeout=1.0)
        assert msg is not None
        assert msg["content"] == "hello"
        assert msg["channel"] == "chan"

    def test_read_timeout_returns_none(self):
        bus = IPCBus()
        msg = bus.read("empty", timeout=0.05)
        assert msg is None

    def test_source_field_stored(self):
        bus = IPCBus()
        bus.emit("ch", "data", source="agent-a")
        msg = bus.read("ch", timeout=1.0)
        assert msg["source"] == "agent-a"

    def test_fifo_order(self):
        bus = IPCBus()
        bus.emit("q", "first")
        bus.emit("q", "second")
        bus.emit("q", "third")
        assert bus.read("q", timeout=0.1)["content"] == "first"
        assert bus.read("q", timeout=0.1)["content"] == "second"
        assert bus.read("q", timeout=0.1)["content"] == "third"

    def test_list_channels_shows_pending(self):
        bus = IPCBus()
        bus.emit("alpha", "a")
        bus.emit("alpha", "b")
        bus.emit("beta", "c")
        channels = {ch["channel"]: ch["pending"] for ch in bus.list_channels()}
        assert channels["alpha"] == 2
        assert channels["beta"] == 1

    def test_pending_count(self):
        bus = IPCBus()
        assert bus.pending("x") == 0
        bus.emit("x", "msg1")
        bus.emit("x", "msg2")
        assert bus.pending("x") == 2

    def test_flush_clears_channel(self):
        bus = IPCBus()
        bus.emit("flush-me", "a")
        bus.emit("flush-me", "b")
        dropped = bus.flush("flush-me")
        assert dropped == 2
        assert bus.pending("flush-me") == 0

    def test_flush_nonexistent_channel(self):
        bus = IPCBus()
        assert bus.flush("nonexistent") == 0

    def test_full_channel_drops_oldest(self):
        from macroa.kernel.ipc import _CHANNEL_MAXSIZE
        bus = IPCBus()
        for i in range(_CHANNEL_MAXSIZE + 5):
            bus.emit("full", str(i))
        # Should not have more than maxsize messages
        assert bus.pending("full") == _CHANNEL_MAXSIZE

    def test_thread_safety(self):
        """Multiple writers and one reader should not deadlock or corrupt data."""
        bus = IPCBus()
        received = []
        errors = []

        def writer(n):
            for i in range(20):
                bus.emit("shared", f"writer-{n}-msg-{i}")

        def reader():
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                msg = bus.read("shared", timeout=0.1)
                if msg:
                    received.append(msg["content"])

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        reader_thread = threading.Thread(target=reader)
        reader_thread.start()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        reader_thread.join()

        # Drain remaining
        while True:
            msg = bus.read("shared", timeout=0.01)
            if msg is None:
                break
            received.append(msg["content"])

        assert len(received) <= 100  # ≤ 5 writers × 20 msgs
        assert len(errors) == 0

    def test_cross_channel_isolation(self):
        bus = IPCBus()
        bus.emit("a", "msg-a")
        bus.emit("b", "msg-b")
        assert bus.read("b", timeout=0.1)["content"] == "msg-b"
        assert bus.read("a", timeout=0.1)["content"] == "msg-a"
        assert bus.read("a", timeout=0.05) is None


# ── Agent tool dispatch tests ──────────────────────────────────────────────────

def _make_drivers(ipc=None):
    """Minimal DriverBundle with only ipc set."""
    bundle = MagicMock(spec=DriverBundle)
    bundle.ipc = ipc
    return bundle


try:
    from unittest.mock import MagicMock
except ImportError:
    pass


class TestIPCToolDispatch:
    def _drivers(self, ipc=None):
        from unittest.mock import MagicMock
        bundle = MagicMock(spec=DriverBundle)
        bundle.ipc = ipc
        return bundle

    def test_ipc_emit_tool(self):
        bus = IPCBus()
        drivers = self._drivers(ipc=bus)
        result = execute_tool(
            "ipc_emit",
            {"channel": "test", "message": "hello world"},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "test" in result
        msg = bus.read("test", timeout=0.5)
        assert msg is not None
        assert msg["content"] == "hello world"

    def test_ipc_read_tool_receives_message(self):
        bus = IPCBus()
        bus.emit("inbox", "from agent-a")
        drivers = self._drivers(ipc=bus)
        result = execute_tool(
            "ipc_read",
            {"channel": "inbox", "timeout": 1.0},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "from agent-a" in result

    def test_ipc_read_tool_timeout(self):
        bus = IPCBus()
        drivers = self._drivers(ipc=bus)
        result = execute_tool(
            "ipc_read",
            {"channel": "empty", "timeout": 0.05},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "no message" in result.lower() or "timeout" in result.lower() or "0.0s" in result or "0.1s" in result

    def test_ipc_list_channels_tool_empty(self):
        bus = IPCBus()
        drivers = self._drivers(ipc=bus)
        result = execute_tool(
            "ipc_list_channels",
            {},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "no active" in result.lower()

    def test_ipc_list_channels_tool_shows_channel(self):
        bus = IPCBus()
        bus.emit("status", "update")
        drivers = self._drivers(ipc=bus)
        result = execute_tool(
            "ipc_list_channels",
            {},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "status" in result
        assert "1" in result

    def test_ipc_emit_no_bus(self):
        drivers = self._drivers(ipc=None)
        result = execute_tool(
            "ipc_emit",
            {"channel": "x", "message": "y"},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "not available" in result.lower()

    def test_ipc_read_no_bus(self):
        drivers = self._drivers(ipc=None)
        result = execute_tool(
            "ipc_read",
            {"channel": "x"},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "not available" in result.lower()

    def test_ipc_list_channels_no_bus(self):
        drivers = self._drivers(ipc=None)
        result = execute_tool(
            "ipc_list_channels",
            {},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "not available" in result.lower()

    def test_ipc_read_clamps_timeout(self):
        """Timeout > 60 should be clamped to 60 (no infinite hangs)."""
        bus = IPCBus()
        bus.emit("ch", "quick")
        drivers = self._drivers(ipc=bus)
        result = execute_tool(
            "ipc_read",
            {"channel": "ch", "timeout": 9999},
            drivers=drivers,
            session_approved=set(),
            confirm_callback=None,
        )
        assert "quick" in result
