"""Deterministic string utilities — no LLM calls."""

from __future__ import annotations

import re

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")
_ESCALATION_HAIKU = re.compile(
    r"\b(use haiku|haiku model|efficiency mode|quick answer)\b", re.IGNORECASE
)
_ESCALATION_SONNET = re.compile(
    r"\b(think carefully|best reasoning|reason carefully|step by step)\b", re.IGNORECASE
)
_ESCALATION_OPUS = re.compile(
    r"\b(opus|best model|most capable|smartest|use the gpu)\b", re.IGNORECASE
)


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[... truncated at {max_chars} chars]"


def detect_escalation_tier(text: str) -> str | None:
    """Return 'opus', 'sonnet', 'haiku', or None based on keyword hints.

    Checked highest-to-lowest so a more expensive tier always wins if both match.
    """
    if _ESCALATION_OPUS.search(text):
        return "opus"
    if _ESCALATION_SONNET.search(text):
        return "sonnet"
    if _ESCALATION_HAIKU.search(text):
        return "haiku"
    return None


def is_shell_prefix(text: str) -> bool:
    """Return True if the input is a hard-routed shell command (! or $ prefix)."""
    stripped = text.strip()
    return stripped.startswith("!") or stripped.startswith("$")


def strip_shell_prefix(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith(("!", "$")):
        return stripped[1:].lstrip()
    return stripped


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())
