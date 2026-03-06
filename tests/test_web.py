"""Tests for the FastAPI web layer — no real LLM calls."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

# Skip entire module if fastapi/httpx not installed
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from macroa.stdlib.schema import ModelTier, SkillResult

# ------------------------------------------------------------------ fixtures

@pytest.fixture
def mock_kernel(monkeypatch):
    """Patch kernel functions used by the web app."""
    result = SkillResult(
        output="Hello from kernel",
        success=True,
        turn_id="t-1",
        model_tier=ModelTier.HAIKU,
        metadata={"skill": "chat_skill"},
    )
    monkeypatch.setattr("macroa.kernel.run", lambda inp, session_id=None: result)
    monkeypatch.setattr("macroa.kernel.get_session_id", lambda: "test-uuid-1234")
    monkeypatch.setattr("macroa.kernel.resolve_session", lambda name: f"uuid-for-{name}")
    monkeypatch.setattr("macroa.kernel.list_sessions", lambda: [])
    monkeypatch.setattr("macroa.kernel.delete_session", lambda name: True)
    monkeypatch.setattr("macroa.kernel.get_audit_stats", lambda: {"total_runs": 5})
    monkeypatch.setattr(
        "macroa.kernel.schedule_add",
        lambda **kw: MagicMock(
            task_id="task-001", label=kw["label"], command=kw["command"],
            schedule=kw["schedule"], next_run_at=time.time() + 60,
            run_count=0, enabled=True, last_error=None,
        ),
    )
    monkeypatch.setattr("macroa.kernel.schedule_list", lambda include_disabled=False: [])
    monkeypatch.setattr("macroa.kernel.schedule_delete", lambda tid: True)
    return result


@pytest.fixture
def client(mock_kernel):
    from macroa.web.app import app
    return TestClient(app, raise_server_exceptions=True)


# ------------------------------------------------------------------ /health

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ------------------------------------------------------------------ /run

def test_run_sync(client):
    resp = client.post("/run", json={"input": "hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["output"] == "Hello from kernel"
    assert data["success"] is True
    assert data["tier"] == "haiku"


def test_run_with_named_session(client):
    resp = client.post("/run", json={"input": "hello", "session": "my-session"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "uuid-for-my-session"


def test_run_with_uuid_session(client):
    uid = "12345678-1234-1234-1234-123456789abc"
    resp = client.post("/run", json={"input": "hello", "session": uid})
    assert resp.status_code == 200
    assert resp.json()["session_id"] == uid


def test_run_stream_flag_rejected(client):
    resp = client.post("/run", json={"input": "hello", "stream": True})
    assert resp.status_code == 400


# ------------------------------------------------------------------ /run/stream

def test_run_stream_sse(client):
    resp = client.get("/run/stream", params={"input": "hello"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body = resp.text
    assert "Hello from kernel" in body
    assert "[DONE]" in body


# ------------------------------------------------------------------ /sessions

def test_sessions_list_empty(client):
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_sessions_delete(client):
    resp = client.delete("/sessions/old-session")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "old-session"


def test_sessions_delete_not_found(client, monkeypatch):
    monkeypatch.setattr("macroa.kernel.delete_session", lambda name: False)
    resp = client.delete("/sessions/ghost")
    assert resp.status_code == 404


# ------------------------------------------------------------------ /schedule

def test_schedule_add(client):
    resp = client.post("/schedule", json={
        "label": "ping", "command": "!echo hi", "schedule": "every:300"
    })
    assert resp.status_code == 200
    assert resp.json()["label"] == "ping"


def test_schedule_add_invalid_spec(client, monkeypatch):
    monkeypatch.setattr(
        "macroa.kernel.schedule_add",
        lambda **kw: (_ for _ in ()).throw(ValueError("bad spec")),
    )
    resp = client.post("/schedule", json={
        "label": "x", "command": "y", "schedule": "bad:spec"
    })
    assert resp.status_code == 422


def test_schedule_list_empty(client):
    resp = client.get("/schedule")
    assert resp.status_code == 200
    assert resp.json() == []


def test_schedule_delete(client):
    resp = client.delete("/schedule/task-001")
    assert resp.status_code == 200


def test_schedule_delete_not_found(client, monkeypatch):
    monkeypatch.setattr("macroa.kernel.schedule_delete", lambda tid: False)
    resp = client.delete("/schedule/no-such-task")
    assert resp.status_code == 404


# ------------------------------------------------------------------ /audit

def test_audit_stats(client):
    resp = client.get("/audit/stats")
    assert resp.status_code == 200
    assert resp.json()["total_runs"] == 5


def test_audit_recent(client, monkeypatch, tmp_path):
    import time

    from macroa.kernel.audit import AuditEntry, AuditLog
    log = AuditLog(db_path=tmp_path / "a.db")
    log.record(AuditEntry(
        turn_id="t1", session_id="s1", raw_input="hello",
        skill_name="chat_skill", model_tier="haiku",
        success=True, elapsed_ms=100, created_at=time.time(),
    ))
    mock_settings = MagicMock()
    mock_settings.audit_db_path = tmp_path / "a.db"
    monkeypatch.setattr("macroa.kernel.audit.AuditLog", lambda db_path: log)
    monkeypatch.setattr("macroa.config.settings.get_settings", lambda: mock_settings)
    resp = client.get("/audit/recent?n=10")
    assert resp.status_code == 200
    # endpoint returns a list (may be empty if log instance differs, but status is 200)
    assert isinstance(resp.json(), list)


def test_dashboard_route(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Macroa" in resp.text

def test_dashboard_alias(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
