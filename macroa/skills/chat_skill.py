"""Chat skill — LLM fallback for general conversation and reasoning."""

from __future__ import annotations

from macroa.drivers.llm_driver import LLMDriverError
from macroa.stdlib.schema import (
    Context, ContextEntry, DriverBundle, Intent, ModelTier, SkillManifest, SkillResult,
)

MANIFEST = SkillManifest(
    name="chat_skill",
    description=(
        "General-purpose conversational AI. Use as the fallback for any request that "
        "doesn't clearly match a more specific skill: questions, explanations, analysis, "
        "creative writing, coding help, and open-ended reasoning."
    ),
    triggers=["chat", "ask", "explain", "help", "what", "how", "why", "tell me"],
    model_tier=None,
    deterministic=False,
)

_SYSTEM_PROMPT = (
    "You are Macroa, a personal AI assistant. "
    "Be concise, accurate, and helpful. "
    "If you are uncertain, say so rather than guessing."
)


def _build_messages(intent: Intent, context: Context) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for entry in context.entries:
        if entry.role in ("user", "assistant"):
            messages.append({"role": entry.role, "content": entry.content})
    messages.append({"role": "user", "content": intent.raw})
    return messages


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    messages = _build_messages(intent, context)
    try:
        response = drivers.llm.complete(
            messages=messages,
            tier=intent.model_tier,
        )
        return SkillResult(
            output=response,
            success=True,
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
            metadata={"skill": "chat_skill", "tier": intent.model_tier.value},
        )
    except LLMDriverError as exc:
        return SkillResult(
            output="",
            success=False,
            error=f"chat_skill LLM error: {exc}",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
    except Exception as exc:
        return SkillResult(
            output="",
            success=False,
            error=f"chat_skill unexpected error: {exc}",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
