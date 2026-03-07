"""Context compactor — summarise evicted context entries into episodic memory.

When the rolling context window drops an old turn, this module compresses it
into a 1-2 sentence episodic memory so the model can still recall what happened
in earlier parts of a long conversation.

Design:
  - Called via ContextManager.on_evict (fire-and-forget daemon thread)
  - Uses NANO tier — cheap, fast, no reasoning needed for compression
  - Stores result via memory_driver.add_episode() (episodes table)
  - Skips trivial entries (< _MIN_CHARS) — "ok", "sure", one-liners not worth keeping
  - Thread-safe: each call spawns an independent daemon thread
"""

from __future__ import annotations

import logging
import threading

from macroa.drivers.llm_driver import LLMDriver
from macroa.drivers.memory_driver import MemoryDriver
from macroa.stdlib.schema import ContextEntry, ModelTier

logger = logging.getLogger(__name__)

_MIN_CHARS = 80      # skip entries shorter than this — not worth compacting
_MAX_CHARS = 3_000   # truncate very long entries before sending to LLM

_SYSTEM_PROMPT = (
    "You are a memory compactor. Your job is to compress a single conversation "
    "turn into 1-2 sentences that preserve any key facts, decisions, topics, or "
    "context that might be relevant in the future. Be concise and factual. "
    "Output only the compressed summary, no preamble."
)


class ContextCompactor:
    """Summarises evicted ContextEntry objects into episodic memory."""

    def __init__(self, llm: LLMDriver, memory: MemoryDriver) -> None:
        self._llm = llm
        self._memory = memory

    # ── public ────────────────────────────────────────────────────────────────

    def handle_eviction(self, entry: ContextEntry) -> None:
        """Called synchronously from ContextManager.on_evict; dispatches to daemon thread."""
        if len(entry.content) < _MIN_CHARS:
            return
        t = threading.Thread(
            target=self._compact,
            args=(entry,),
            daemon=True,
            name=f"macroa-compactor-{entry.turn_id[:8]}",
        )
        t.start()

    # ── internal ──────────────────────────────────────────────────────────────

    def _compact(self, entry: ContextEntry) -> None:
        if len(entry.content) < _MIN_CHARS:
            return
        try:
            content = entry.content[:_MAX_CHARS]
            summary = self._llm.complete(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"[{entry.role}] {content}"},
                ],
                tier=ModelTier.NANO,
                max_tokens=128,
            )
            if not summary or len(summary.strip()) < 10:
                return
            self._memory.add_episode(
                session_id=entry.turn_id,   # use turn_id as session surrogate for episodes
                summary=summary.strip(),
                tags=["compacted_context", entry.role],
                turn_count=1,
            )
            logger.debug("Compacted evicted %s entry (turn %s…)", entry.role, entry.turn_id[:8])
        except Exception as exc:
            logger.debug("Compaction failed for turn %s: %s", entry.turn_id[:8], exc)
