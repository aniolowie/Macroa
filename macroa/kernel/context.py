"""Rolling context window — kernel owns all mutation."""

from __future__ import annotations

import uuid
from collections import deque

from macroa.stdlib.schema import Context, ContextEntry, SkillResult


class ContextManager:
    def __init__(self, session_id: str | None = None, window_size: int = 20) -> None:
        self.session_id = session_id or str(uuid.uuid4())
        # window_size turns × 2 entries (user + assistant) each
        self._buffer: deque[ContextEntry] = deque(maxlen=window_size * 2)

    # ------------------------------------------------------------------ read

    def snapshot(self) -> Context:
        """Return an immutable snapshot of current context."""
        return Context(entries=list(self._buffer), session_id=self.session_id)

    # ------------------------------------------------------------------ write

    def add_user(self, turn_id: str, content: str) -> None:
        self._add(ContextEntry(turn_id=turn_id, role="user", content=content))

    def add_assistant(self, result: SkillResult) -> None:
        pinned = result.pin_to_context
        entry = ContextEntry(
            turn_id=result.turn_id,
            role="assistant",
            content=result.output,
            pinned=pinned,
            skill_name=result.metadata.get("skill"),
        )
        self._add(entry)

    def add_system(self, turn_id: str, content: str, pinned: bool = False) -> None:
        self._add(ContextEntry(turn_id=turn_id, role="system", content=content, pinned=pinned))

    def clear(self) -> None:
        self._buffer.clear()

    # ------------------------------------------------------------------ internal

    def _add(self, entry: ContextEntry) -> None:
        if len(self._buffer) == self._buffer.maxlen:
            self._evict_oldest_unpinned()
        self._buffer.append(entry)

    def _evict_oldest_unpinned(self) -> None:
        """Drop the oldest non-pinned entry to make room."""
        for i, e in enumerate(self._buffer):
            if not e.pinned:
                # deque doesn't support O(1) arbitrary deletion — rebuild
                entries = list(self._buffer)
                del entries[i]
                self._buffer.clear()
                self._buffer.extend(entries)
                return
        # all entries pinned — let the deque evict from the left naturally
