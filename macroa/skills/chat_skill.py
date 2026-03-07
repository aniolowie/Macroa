"""Chat skill — LLM fallback for general conversation and reasoning."""

from __future__ import annotations

from macroa.drivers.llm_driver import LLMDriverError
from macroa.kernel.clock import now_context
from macroa.kernel.identity import build_system_prompt
from macroa.memory.formatter import format_for_prompt
from macroa.memory.retriever import retrieve
from macroa.stdlib.schema import (
    Context,
    DriverBundle,
    Intent,
    SkillManifest,
    SkillResult,
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


def _build_system(intent: Intent, drivers: DriverBundle) -> str:
    """Build system prompt: current time + identity + contextually retrieved memory."""
    # Always prepend real current time — prevents LLM from hallucinating time/date
    try:
        time_line = now_context(drivers.memory)
    except Exception:
        time_line = ""

    base = build_system_prompt()

    try:
        facts = retrieve(intent.raw, drivers.memory)
        memory_block = format_for_prompt(facts)
        if memory_block:
            base = base + "\n\n" + memory_block
    except Exception:
        pass

    return (time_line + "\n\n" + base) if time_line else base


def _build_messages(
    intent: Intent, context: Context, drivers: DriverBundle
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": _build_system(intent, drivers)}]
    for entry in context.entries:
        if entry.role in ("user", "assistant"):
            messages.append({"role": entry.role, "content": entry.content})
    messages.append({"role": "user", "content": intent.raw})
    return messages


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    messages = _build_messages(intent, context, drivers)
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
