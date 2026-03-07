"""MemoryRetriever — pre-turn contextual memory fetch.

Two-bucket strategy:
  1. Pinned facts    — always injected regardless of query (core user profile, max 20)
  2. Contextual facts — FTS5 search against the current query, top-K, deduped

Results are merged, deduplicated by key, and capped at a character budget
to avoid bloating the prompt. Pinned facts are always kept; contextual facts
are trimmed from the tail when the budget is exceeded.
"""

from __future__ import annotations

from macroa.drivers.memory_driver import MemoryDriver

# ~500 tokens ≈ 2 000 characters (4 chars/token heuristic)
_CHAR_BUDGET = 2_000
# Number of contextual (non-pinned) FTS5 results to consider
_CONTEXT_K = 8

# Queries that mean "tell me everything about me" — FTS is useless here because
# the words "describe" / "who" don't appear in fact keys or values.
_SELF_REFERENTIAL = frozenset([
    "describe me", "describe who", "who am i", "who i am", "about myself",
    "about me", "know about me", "what you know", "what do you know",
    "tell me about me", "tell me about myself", "what i told you",
])


def _is_self_referential(query: str) -> bool:
    q = query.lower()
    return any(sig in q for sig in _SELF_REFERENTIAL)


def retrieve(query: str, memory: MemoryDriver) -> list[dict]:
    """Return an ordered list of fact dicts relevant to the current query.

    Pinned facts come first (always included up to budget).
    Contextual facts follow (FTS5-ranked, deduplicated against pinned).
    """
    # Self-referential queries ("describe me", "who am i") — FTS is useless
    # because query words don't appear in user fact keys/values. Return everything.
    if _is_self_referential(query):
        return memory.list_all()[:20]

    pinned = memory.list_pinned()
    pinned_keys = {f["key"] for f in pinned}

    contextual = [
        f for f in memory.search_fts(query, limit=_CONTEXT_K)
        if f["key"] not in pinned_keys
    ]

    merged = pinned + contextual

    # Enforce character budget — trim contextual tail first
    kept: list[dict] = []
    used = 0
    for fact in merged:
        cost = len(fact["key"]) + len(fact["value"]) + 6  # "- **key**: value\n"
        if used + cost > _CHAR_BUDGET and not fact.get("pinned"):
            # Never drop pinned facts; drop contextual facts when over budget
            continue
        kept.append(fact)
        used += cost

    return kept
