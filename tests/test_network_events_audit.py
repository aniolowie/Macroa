"""Tests for NetworkDriver, EventBus, and AuditLog."""

from __future__ import annotations

import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from macroa.drivers.network_driver import NetworkDriver, NetworkResponse
from macroa.kernel.audit import AuditEntry, AuditLog
from macroa.kernel.events import Event, EventBus

# ================================================================== NetworkDriver

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # silence server logs

    def do_GET(self):
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        elif self.path == "/404":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)  # echo back


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def test_get_success(server):
    driver = NetworkDriver(timeout=5)
    resp = driver.get(f"{server}/ok")
    assert resp.success
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_get_404(server):
    driver = NetworkDriver(timeout=5)
    resp = driver.get(f"{server}/404")
    assert not resp.success
    assert resp.status_code == 404


def test_post_json_echo(server):
    driver = NetworkDriver(timeout=5)
    resp = driver.post(f"{server}/", json={"key": "value"})
    assert resp.success
    assert resp.json()["key"] == "value"


def test_network_bad_url():
    driver = NetworkDriver(timeout=2)
    resp = driver.get("http://this-host-does-not-exist.invalid/")
    assert not resp.success
    assert resp.error is not None


def test_json_safe_default():
    resp = NetworkResponse(status_code=200, body="not json", headers={}, success=True)
    assert resp.json_safe(default="fallback") == "fallback"


def test_json_parse():
    resp = NetworkResponse(status_code=200, body='{"a":1}', headers={}, success=True)
    assert resp.json()["a"] == 1


# ================================================================== EventBus

def test_subscribe_and_emit():
    bus = EventBus()
    received = []
    bus.subscribe("test.event", lambda e: received.append(e))
    bus.emit(Event(event_type="test.event", source="test"))
    assert len(received) == 1
    assert received[0].event_type == "test.event"


def test_wildcard_subscriber():
    bus = EventBus()
    all_events = []
    bus.subscribe_all(lambda e: all_events.append(e))
    bus.emit(Event(event_type="a", source="x"))
    bus.emit(Event(event_type="b", source="y"))
    assert len(all_events) == 2


def test_unsubscribe():
    bus = EventBus()
    calls = []

    def handler(e):
        calls.append(e)

    bus.subscribe("evt", handler)
    bus.emit(Event(event_type="evt", source="x"))
    bus.unsubscribe("evt", handler)
    bus.emit(Event(event_type="evt", source="x"))
    assert len(calls) == 1


def test_failing_handler_doesnt_kill_bus():
    bus = EventBus()
    results = []

    def bad_handler(e): raise RuntimeError("boom")
    def good_handler(e): results.append(e)

    bus.subscribe("evt", bad_handler)
    bus.subscribe("evt", good_handler)
    bus.emit(Event(event_type="evt", source="x"))
    assert len(results) == 1  # good handler still ran


def test_event_payload():
    bus = EventBus()
    received = []
    bus.subscribe("evt", lambda e: received.append(e))
    bus.emit(Event(event_type="evt", source="kernel", payload={"key": "val"}))
    assert received[0].payload["key"] == "val"


def test_no_handlers_is_safe():
    bus = EventBus()
    bus.emit(Event(event_type="unknown", source="x"))  # should not raise


def test_thread_safe_emit():
    """Multiple threads can emit concurrently without crashing."""
    bus = EventBus()
    counts = []
    bus.subscribe_all(lambda e: counts.append(1))

    def emit_many():
        for _ in range(50):
            bus.emit(Event(event_type="t", source="thread"))

    threads = [threading.Thread(target=emit_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(counts) == 200


# ================================================================== AuditLog

def _entry(**kwargs) -> AuditEntry:
    defaults = dict(
        turn_id=str(uuid.uuid4()),
        session_id="sess-1",
        raw_input="test input",
        skill_name="chat_skill",
        model_tier="nano",
        success=True,
        elapsed_ms=42,
    )
    defaults.update(kwargs)
    return AuditEntry(**defaults)


def test_audit_record_and_retrieve(tmp_path):
    log = AuditLog(db_path=tmp_path / "audit.db")
    log.record(_entry())
    entries = log.recent(10)
    assert len(entries) == 1
    assert entries[0].skill_name == "chat_skill"


def test_audit_recent_limit(tmp_path):
    log = AuditLog(db_path=tmp_path / "audit.db")
    for i in range(10):
        log.record(_entry(raw_input=f"input {i}"))
    assert len(log.recent(5)) == 5


def test_audit_filter_by_session(tmp_path):
    log = AuditLog(db_path=tmp_path / "audit.db")
    log.record(_entry(session_id="s1"))
    log.record(_entry(session_id="s2"))
    s1_only = log.recent(10, session_id="s1")
    assert all(e.session_id == "s1" for e in s1_only)
    assert len(s1_only) == 1


def test_audit_failure_recorded(tmp_path):
    log = AuditLog(db_path=tmp_path / "audit.db")
    log.record(_entry(success=False, error="something broke"))
    entries = log.recent(1)
    assert not entries[0].success
    assert entries[0].error == "something broke"


def test_audit_stats(tmp_path):
    log = AuditLog(db_path=tmp_path / "audit.db")
    log.record(_entry(skill_name="shell_skill", model_tier="nano", success=True))
    log.record(_entry(skill_name="chat_skill",  model_tier="haiku", success=True))
    log.record(_entry(skill_name="chat_skill",  model_tier="nano",  success=False))
    log.record(_entry(skill_name="planner",     model_tier="haiku", plan_steps=3))

    stats = log.stats()
    assert stats["total_runs"] == 4
    assert stats["failures"] == 1
    assert stats["plan_calls"] == 1
    # sessions uses COUNT(DISTINCT session_id) — all four entries share sess-1
    assert stats["sessions"] == 1
    skill_names = [s["skill"] for s in stats["by_skill"]]
    assert "chat_skill" in skill_names
    assert "shell_skill" in skill_names


def test_audit_stats_session_count_is_exact(tmp_path):
    """sessions count must reflect the true distinct count, not a sampled subset."""
    log = AuditLog(db_path=tmp_path / "audit.db")
    # Insert entries across 3 distinct sessions
    for i in range(3):
        for _ in range(5):  # 5 turns each = 15 total entries
            log.record(_entry(session_id=f"sess-{i}"))

    stats = log.stats()
    assert stats["total_runs"] == 15
    assert stats["sessions"] == 3


def test_audit_input_capped_at_1000_chars(tmp_path):
    log = AuditLog(db_path=tmp_path / "audit.db")
    log.record(_entry(raw_input="x" * 5000))
    entries = log.recent(1)
    assert len(entries[0].raw_input) <= 1000
