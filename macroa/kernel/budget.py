"""Session Budget — token/cost quota system (ulimit for AI agents).

Every LLM call burns tokens. Without a cap, a runaway agent loop
(bad tool result, stuck reasoning, adversarial prompt) can exhaust
the user's API quota or rack up unexpected costs.

This module tracks spend per session and signals the kernel when
a configured limit is reached. The response is always graceful —
the agent is forced to summarise what it has done so far rather
than hard-crashed.

Configuration (in ~/.macroa/.env or shell env):
    MACROA_SESSION_BUDGET_USD=0.10      # max USD per session (0 = unlimited)
    MACROA_SESSION_BUDGET_TOKENS=50000  # max tokens per session (0 = unlimited)

Model prices are approximate and used only for cost estimation.
They do not affect routing or model selection.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Price table (USD per 1M tokens, input/output) ────────────────────────────
# Kept here — single source of truth for cost estimation across the kernel.
# Add new models as needed; unknown models fall back to _DEFAULT_PRICE.

_PRICE_PER_1M: dict[str, tuple[float, float]] = {
    # Nano tier
    "google/gemini-2.5-flash-lite":  (0.10,   0.40),
    "openai/gpt-5-nano":             (0.075,  0.30),
    "deepseek/deepseek-v3.2":        (0.27,   1.10),
    # Haiku tier
    "google/gemini-2.5-flash":       (0.15,   0.60),
    "openai/gpt-5-mini":             (0.40,   1.60),
    "anthropic/claude-haiku-4-5":    (0.80,   4.00),
    # Sonnet tier
    "anthropic/claude-sonnet-4-6":   (3.00,  15.00),
    "openai/gpt-5":                  (3.44,  13.75),
    # Opus tier
    "anthropic/claude-opus-4-6":     (15.00, 75.00),
}

_DEFAULT_PRICE: tuple[float, float] = (1.00, 5.00)  # conservative fallback


def estimate_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    """Return estimated USD cost for one LLM call."""
    input_per_1m, output_per_1m = _PRICE_PER_1M.get(model, _DEFAULT_PRICE)
    return (prompt_tokens * input_per_1m + completion_tokens * output_per_1m) / 1_000_000


# ── Per-session tracker ───────────────────────────────────────────────────────

@dataclass
class SessionBudget:
    """Tracks cumulative spend for one session."""
    budget_usd: float      # 0 = unlimited
    budget_tokens: int     # 0 = unlimited
    spent_usd: float = 0.0
    spent_tokens: int = 0
    call_count: int = 0

    def record(self, prompt_tokens: int, completion_tokens: int, model: str) -> None:
        cost = estimate_cost(prompt_tokens, completion_tokens, model)
        self.spent_usd += cost
        self.spent_tokens += prompt_tokens + completion_tokens
        self.call_count += 1
        logger.debug(
            "Budget: +%dt (+$%.5f) → %dt / $%.4f total",
            prompt_tokens + completion_tokens, cost,
            self.spent_tokens, self.spent_usd,
        )

    def over_budget(self) -> bool:
        if self.budget_usd > 0 and self.spent_usd >= self.budget_usd:
            return True
        if self.budget_tokens > 0 and self.spent_tokens >= self.budget_tokens:
            return True
        return False

    def remaining_usd(self) -> float:
        if self.budget_usd <= 0:
            return float("inf")
        return max(0.0, self.budget_usd - self.spent_usd)

    def remaining_tokens(self) -> int:
        if self.budget_tokens <= 0:
            return -1  # unlimited sentinel
        return max(0, self.budget_tokens - self.spent_tokens)

    def summary(self) -> dict:
        return {
            "spent_usd": round(self.spent_usd, 6),
            "spent_tokens": self.spent_tokens,
            "budget_usd": self.budget_usd,
            "budget_tokens": self.budget_tokens,
            "remaining_usd": self.remaining_usd(),
            "remaining_tokens": self.remaining_tokens(),
            "call_count": self.call_count,
            "over_budget": self.over_budget(),
        }


# ── Kernel-level manager ──────────────────────────────────────────────────────

class BudgetManager:
    """Manages per-session budgets. Kernel singleton.

    Thread-safe — sessions run concurrently (scheduler, watchdog, user).
    """

    def __init__(self, budget_usd: float = 0.0, budget_tokens: int = 0) -> None:
        self._budget_usd = budget_usd
        self._budget_tokens = budget_tokens
        self._sessions: dict[str, SessionBudget] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._budget_usd > 0 or self._budget_tokens > 0

    def get(self, session_id: str) -> SessionBudget:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionBudget(
                    budget_usd=self._budget_usd,
                    budget_tokens=self._budget_tokens,
                )
            return self._sessions[session_id]

    def record(self, session_id: str, prompt_tokens: int, completion_tokens: int, model: str) -> None:
        self.get(session_id).record(prompt_tokens, completion_tokens, model)

    def is_over(self, session_id: str) -> bool:
        return self.get(session_id).over_budget()

    def stats(self, session_id: str) -> dict:
        return self.get(session_id).summary()

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
