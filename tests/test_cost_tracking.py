"""Tests for cost tracking: audit schema migration, token recording, stats."""

from __future__ import annotations

from pathlib import Path


# ── AuditEntry fields ─────────────────────────────────────────────────────────


def test_audit_entry_has_cost_fields():
    from macroa.kernel.audit import AuditEntry

    e = AuditEntry(
        turn_id="t1", session_id="s1", raw_input="hi",
        skill_name="chat_skill", model_tier="sonnet",
        success=True, elapsed_ms=100,
    )
    assert e.prompt_tokens == 0
    assert e.completion_tokens == 0
    assert e.cost_usd == 0.0


def test_audit_entry_stores_cost():
    from macroa.kernel.audit import AuditEntry

    e = AuditEntry(
        turn_id="t1", session_id="s1", raw_input="hi",
        skill_name="chat_skill", model_tier="sonnet",
        success=True, elapsed_ms=100,
        prompt_tokens=500, completion_tokens=250, cost_usd=0.0045,
    )
    assert e.prompt_tokens == 500
    assert e.completion_tokens == 250
    assert e.cost_usd == pytest.approx(0.0045)


# ── AuditLog schema and round-trip ────────────────────────────────────────────


import pytest


def test_audit_log_records_and_reads_cost(tmp_path: Path):
    from macroa.kernel.audit import AuditEntry, AuditLog

    log = AuditLog(db_path=tmp_path / "audit.db")
    log.record(AuditEntry(
        turn_id="t1", session_id="s1", raw_input="test",
        skill_name="chat_skill", model_tier="sonnet",
        success=True, elapsed_ms=200,
        prompt_tokens=1000, completion_tokens=500, cost_usd=0.009,
    ))

    entries = log.recent(1)
    assert len(entries) == 1
    e = entries[0]
    assert e.prompt_tokens == 1000
    assert e.completion_tokens == 500
    assert e.cost_usd == pytest.approx(0.009)


def test_audit_stats_include_total_cost(tmp_path: Path):
    from macroa.kernel.audit import AuditEntry, AuditLog

    log = AuditLog(db_path=tmp_path / "audit.db")
    log.record(AuditEntry(
        turn_id="t1", session_id="s1", raw_input="a",
        skill_name="chat_skill", model_tier="sonnet",
        success=True, elapsed_ms=100,
        prompt_tokens=100, completion_tokens=50, cost_usd=0.0009,
    ))
    log.record(AuditEntry(
        turn_id="t2", session_id="s1", raw_input="b",
        skill_name="chat_skill", model_tier="haiku",
        success=True, elapsed_ms=80,
        prompt_tokens=200, completion_tokens=100, cost_usd=0.0006,
    ))

    stats = log.stats()
    assert stats["total_cost_usd"] == pytest.approx(0.0015)
    assert stats["total_tokens"] == 450
    assert stats["total_runs"] == 2


def test_audit_stats_cost_by_tier(tmp_path: Path):
    from macroa.kernel.audit import AuditEntry, AuditLog

    log = AuditLog(db_path=tmp_path / "audit.db")
    log.record(AuditEntry(
        turn_id="t1", session_id="s1", raw_input="x",
        skill_name="chat_skill", model_tier="opus",
        success=True, elapsed_ms=300,
        prompt_tokens=500, completion_tokens=200, cost_usd=0.007,
    ))

    stats = log.stats()
    tier_row = next((r for r in stats["by_tier"] if r["tier"] == "opus"), None)
    assert tier_row is not None
    assert tier_row["cost_usd"] == pytest.approx(0.007)


def test_audit_migration_adds_columns_to_old_schema(tmp_path: Path):
    """Simulate a pre-cost-tracking database and verify migration adds columns."""
    import sqlite3
    from macroa.kernel.audit import AuditEntry, AuditLog

    db_path = tmp_path / "old.db"
    # Create a table without cost columns (simulating old schema)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT NOT NULL, session_id TEXT NOT NULL,
                raw_input TEXT NOT NULL, skill_name TEXT NOT NULL,
                model_tier TEXT NOT NULL, success INTEGER NOT NULL,
                elapsed_ms INTEGER NOT NULL, plan_steps INTEGER NOT NULL DEFAULT 0,
                error TEXT, created_at REAL NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO audit_log VALUES (1,'t0','s0','old input','chat_skill','sonnet',1,100,0,NULL,1700000000.0)"
        )

    # Opening AuditLog should migrate silently
    log = AuditLog(db_path=db_path)
    entries = log.recent(5)
    assert len(entries) == 1
    assert entries[0].prompt_tokens == 0  # default applied by migration
    assert entries[0].cost_usd == 0.0

    # New records should store cost
    log.record(AuditEntry(
        turn_id="t1", session_id="s1", raw_input="new",
        skill_name="chat_skill", model_tier="sonnet",
        success=True, elapsed_ms=50,
        prompt_tokens=300, completion_tokens=150, cost_usd=0.0027,
    ))
    new = log.recent(1)
    assert new[0].cost_usd == pytest.approx(0.0027)


# ── _compute_cost ─────────────────────────────────────────────────────────────


def test_compute_cost_known_model():
    from macroa.kernel import _compute_cost

    prompt, completion, cost = _compute_cost({
        "model": "anthropic/claude-sonnet-4-6",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
    })
    assert prompt == 1000
    assert completion == 500
    # 1500 tokens @ $6.00/M = 0.0015M × $6 = $0.009
    assert cost == pytest.approx(0.009)


def test_compute_cost_unknown_model_is_zero():
    from macroa.kernel import _compute_cost

    _, _, cost = _compute_cost({"model": "some/unknown-model", "prompt_tokens": 999})
    assert cost == 0.0


def test_compute_cost_empty_usage():
    from macroa.kernel import _compute_cost

    prompt, completion, cost = _compute_cost({})
    assert prompt == 0
    assert completion == 0
    assert cost == 0.0


def test_compute_cost_nano_model():
    from macroa.kernel import _compute_cost

    _, _, cost = _compute_cost({
        "model": "google/gemini-2.5-flash-lite",
        "prompt_tokens": 1_000_000,
        "completion_tokens": 0,
    })
    assert cost == pytest.approx(0.18)
