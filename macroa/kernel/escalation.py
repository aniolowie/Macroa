"""Escalation logic — tier selection and promotion.

Hardware analogy (low → high):
  NANO   = microcontroller   (background, routing)
  HAIKU  = efficiency cores  (E-cores, lightweight)
  SONNET = performance cores (P-cores, quality)
  OPUS   = GPU               (heavy reasoning, use sparingly)
"""

from __future__ import annotations

from macroa.stdlib.schema import ModelTier
from macroa.stdlib.text import detect_escalation_tier

_TIER_ORDER = [ModelTier.NANO, ModelTier.HAIKU, ModelTier.SONNET, ModelTier.OPUS]

_KEYWORD_TIER: dict[str, ModelTier] = {
    "haiku": ModelTier.HAIKU,
    "sonnet": ModelTier.SONNET,
    "opus": ModelTier.OPUS,
}


def resolve_tier(
    raw_input: str,
    skill_pinned_tier: ModelTier | None,
) -> ModelTier:
    """Determine the starting model tier for a request.

    Priority:
      1. Keyword override in the raw input (user explicitly requests a tier)
      2. Skill's pinned tier
      3. Default: NANO
    """
    keyword = detect_escalation_tier(raw_input)
    if keyword and keyword in _KEYWORD_TIER:
        return _KEYWORD_TIER[keyword]

    if skill_pinned_tier is not None:
        return skill_pinned_tier

    return ModelTier.NANO


def next_tier(current: ModelTier) -> ModelTier:
    """Return the next tier up, or stay at OPUS (the ceiling)."""
    try:
        idx = _TIER_ORDER.index(current)
    except ValueError:
        return ModelTier.OPUS
    if idx < len(_TIER_ORDER) - 1:
        return _TIER_ORDER[idx + 1]
    return current
