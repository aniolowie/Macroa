"""Tests for BudgetManager and AgentLoop budget integration."""

from __future__ import annotations

from macroa.kernel.budget import BudgetManager, SessionBudget

# ── SessionBudget unit tests ───────────────────────────────────────────────────

class TestSessionBudget:
    def test_no_limits_never_over(self):
        b = SessionBudget(budget_usd=0, budget_tokens=0)
        b.record(100_000, 100_000, "anthropic/claude-opus-4-6")
        assert not b.over_budget()

    def test_token_limit_triggers(self):
        b = SessionBudget(budget_usd=0, budget_tokens=500)
        b.record(300, 300, "google/gemini-2.5-flash-lite")
        assert b.over_budget()

    def test_token_limit_not_yet(self):
        b = SessionBudget(budget_usd=0, budget_tokens=1000)
        b.record(300, 300, "google/gemini-2.5-flash-lite")
        assert not b.over_budget()

    def test_usd_limit_triggers(self):
        b = SessionBudget(budget_usd=0.0001, budget_tokens=0)
        # claude-opus-4-6: $15/M input, $75/M output
        # 1M prompt tokens = $15 → well over 0.0001
        b.record(1_000_000, 0, "anthropic/claude-opus-4-6")
        assert b.over_budget()

    def test_usd_limit_not_yet(self):
        b = SessionBudget(budget_usd=100.0, budget_tokens=0)
        b.record(100, 100, "anthropic/claude-sonnet-4-6")
        assert not b.over_budget()

    def test_unknown_model_uses_fallback_price(self):
        # Unknown models fall back to _DEFAULT_PRICE = ($1/$5 per 1M)
        # so large token counts still produce a non-zero cost estimate
        b = SessionBudget(budget_usd=100.0, budget_tokens=0)
        b.record(1_000, 1_000, "some/unknown-model")
        # cost ≈ $0.006 — well under $100 budget
        assert not b.over_budget()
        assert b.spent_usd > 0

    def test_call_count_increments(self):
        b = SessionBudget(budget_usd=0, budget_tokens=0)
        b.record(10, 10, "google/gemini-2.5-flash-lite")
        b.record(10, 10, "google/gemini-2.5-flash-lite")
        assert b.call_count == 2

    def test_summary_keys(self):
        b = SessionBudget(budget_usd=1.0, budget_tokens=10_000)
        b.record(100, 50, "google/gemini-2.5-flash-lite")
        s = b.summary()
        assert "spent_usd" in s
        assert "spent_tokens" in s
        assert "call_count" in s
        assert s["spent_tokens"] == 150


# ── BudgetManager unit tests ───────────────────────────────────────────────────

class TestBudgetManager:
    def test_get_creates_new_session(self):
        mgr = BudgetManager(budget_usd=1.0, budget_tokens=5000)
        b = mgr.get("sess-1")
        assert isinstance(b, SessionBudget)
        assert b.budget_usd == 1.0
        assert b.budget_tokens == 5000

    def test_get_returns_same_object(self):
        mgr = BudgetManager(budget_usd=1.0, budget_tokens=0)
        b1 = mgr.get("sess-a")
        b2 = mgr.get("sess-a")
        assert b1 is b2

    def test_record_delegates(self):
        mgr = BudgetManager(budget_usd=0, budget_tokens=100)
        mgr.record("s1", 60, 60, "google/gemini-2.5-flash-lite")
        assert mgr.get("s1").spent_tokens == 120

    def test_is_over_false_initially(self):
        mgr = BudgetManager(budget_usd=10.0, budget_tokens=10_000)
        assert not mgr.is_over("new-session")

    def test_is_over_true_after_exceeding(self):
        mgr = BudgetManager(budget_usd=0, budget_tokens=10)
        mgr.record("s2", 6, 6, "google/gemini-2.5-flash-lite")
        assert mgr.is_over("s2")

    def test_stats_returns_dict(self):
        mgr = BudgetManager(budget_usd=1.0, budget_tokens=0)
        mgr.record("s3", 100, 50, "anthropic/claude-sonnet-4-6")
        stats = mgr.stats("s3")
        assert isinstance(stats, dict)
        assert "spent_tokens" in stats

    def test_zero_limits_never_over(self):
        mgr = BudgetManager(budget_usd=0, budget_tokens=0)
        mgr.record("s4", 10_000_000, 10_000_000, "anthropic/claude-opus-4-6")
        assert not mgr.is_over("s4")

    def test_independent_sessions(self):
        mgr = BudgetManager(budget_usd=0, budget_tokens=50)
        mgr.record("sess-x", 30, 30, "google/gemini-2.5-flash-lite")
        mgr.record("sess-y", 5, 5, "google/gemini-2.5-flash-lite")
        assert mgr.is_over("sess-x")
        assert not mgr.is_over("sess-y")
