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


def retrieve(query: str, memory: MemoryDriver) -> list[dict]:
    """Return an ordered list of fact dicts relevant to the current query.

    Pinned facts come first (always included up to budget).
    Contextual facts follow (FTS5-ranked, deduplicated against pinned).
    """
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
