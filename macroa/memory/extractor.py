"""MemoryExtractor — post-turn fact extraction using the NANO model.

Runs in a daemon thread after every conversation turn so it never blocks
the user-facing response. Extracts user facts and upserts them into the
MemoryDriver. Failures are logged silently — extraction is always best-effort.

Design invariants:
  - Never raises; all errors are swallowed at the thread boundary
  - Only runs when the user message is substantive (>= MIN_WORDS words)
  - Only overwrites an existing fact when the new confidence >= existing confidence
  - Pinned status is preserved: once pinned by the user it stays pinned even if
    the extractor re-encounters the same key with pinned=False
"""

from __future__ import annotations

import json
import logging
import re
import threading

from macroa.drivers.llm_driver import LLMDriver
from macroa.drivers.memory_driver import MemoryDriver
from macroa.stdlib.schema import ModelTier

logger = logging.getLogger(__name__)

# Minimum words in the user message before we bother extracting
_MIN_WORDS = 10

_SYSTEM_PROMPT = """\
You are a precision memory extraction engine for a personal AI assistant.

Given a single conversation exchange (one user message + one assistant reply),
extract facts about the USER worth storing permanently in long-term memory.

EXTRACT only:
  - Identity    : name, age, location, timezone, occupation, primary language
  - Preferences : tools, frameworks, communication style, formats, UI preferences
  - Current work: active projects, goals, blockers, deadlines
  - Relationships: recurring people (colleagues, family) worth remembering
  - Long-term context: lifestyle, constraints, recurring patterns

DO NOT EXTRACT:
  - Transient requests ("what's 2+2", "search for X")
  - The assistant's statements or opinions
  - Anything already obvious / generic ("user uses a computer")
  - Questions the user asked without answering

Output a JSON array where each element is:
{
  "key":        "snake_case_identifier",
  "value":      "concise string, max 150 chars",
  "confidence": <float 0.0–1.0>,
  "pinned":     <bool>
}

confidence rules:
  1.0  → user stated it explicitly ("my name is X", "I use Python")
  0.85 → strongly implied ("building a Python project" → primary_language=Python)
  0.7  → reasonably inferred from context

pinned=true ONLY for core identity facts that should always appear in context:
  name, location, timezone, occupation, primary_language, primary_framework

Return [] if there is nothing worth extracting.
Return JSON only. No prose, no markdown fences, no explanation.\
"""


class MemoryExtractor:
    """Extracts and persists user facts from conversation turns."""

    def __init__(self, llm: LLMDriver, memory: MemoryDriver) -> None:
        self._llm = llm
        self._memory = memory

    def extract_async(self, user_msg: str, assistant_msg: str) -> None:
        """Fire-and-forget: start extraction in a daemon thread and return immediately."""
        if len(user_msg.split()) < _MIN_WORDS:
            return
        thread = threading.Thread(
            target=self._run,
            args=(user_msg, assistant_msg),
            daemon=True,
            name="memory-extractor",
        )
        thread.start()

    def _run(self, user_msg: str, assistant_msg: str) -> None:
        if len(user_msg.split()) < _MIN_WORDS:
            return
        try:
            exchange = f"User: {user_msg}\nAssistant: {assistant_msg}"
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": exchange},
            ]
            raw = self._llm.complete(messages=messages, tier=ModelTier.NANO)
            facts = _parse_facts(raw)

            written = 0
            for fact in facts:
                key = fact.get("key", "").strip()
                value = fact.get("value", "").strip()
                if not key or not value:
                    continue
                # Clamp value length
                if len(value) > 150:
                    value = value[:147] + "…"

                confidence = max(0.0, min(1.0, float(fact.get("confidence", 1.0))))
                new_pinned = bool(fact.get("pinned", False))

                # Don't downgrade existing facts with higher confidence
                existing = self._memory.get_fact("user", key)
                if existing and existing.confidence > confidence:
                    continue

                # Preserve user-set pinned status — never unpins what the user pinned
                if existing and existing.pinned and not new_pinned:
                    new_pinned = True

                self._memory.set_fact(
                    namespace="user",
                    key=key,
                    value=value,
                    confidence=confidence,
                    source="extracted",
                    pinned=new_pinned,
                )
                written += 1

            if written:
                logger.debug("memory-extractor: wrote %d facts", written)

        except Exception as exc:
            logger.debug("memory-extractor: non-fatal error: %s", exc)


def _parse_facts(raw: str) -> list[dict]:
    """Robustly parse a JSON array from the model response.

    Handles markdown fences, leading/trailing prose, and malformed JSON.
    """
    text = raw.strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    # Grab the first [...] block
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
        return [d for d in data if isinstance(d, dict)]
    except json.JSONDecodeError:
        return []
