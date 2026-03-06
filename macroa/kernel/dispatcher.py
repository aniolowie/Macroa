"""Dispatcher — runs a skill with escalation loop."""

from __future__ import annotations

import dataclasses
import logging

from macroa.config.skill_registry import SkillRegistry
from macroa.kernel.escalation import next_tier
from macroa.stdlib.schema import Context, DriverBundle, Intent, SkillResult

logger = logging.getLogger(__name__)

_MAX_ESCALATIONS = 2


class Dispatcher:
    def __init__(self, registry: SkillRegistry, drivers: DriverBundle) -> None:
        self._registry = registry
        self._drivers = drivers

    def dispatch(self, intent: Intent, context: Context) -> SkillResult:
        current_tier = intent.model_tier
        current_intent = intent

        for attempt in range(_MAX_ESCALATIONS + 1):
            entry = self._registry.get(current_intent.skill_name)
            if entry is None:
                logger.warning(
                    "Skill %r not found — falling back to chat_skill",
                    current_intent.skill_name,
                )
                entry = self._registry.get("chat_skill")
                if entry is None:
                    return SkillResult(
                        output="",
                        success=False,
                        error=f"Skill {current_intent.skill_name!r} not found and chat_skill unavailable",
                        turn_id=intent.turn_id,
                        model_tier=current_tier,
                    )

            logger.debug(
                "Dispatching to %s (tier=%s, attempt=%d)",
                entry.manifest.name,
                current_tier.value,
                attempt,
            )

            result = entry.run(current_intent, context, self._drivers)

            if not result.needs_reasoning:
                return result

            # Escalate
            new_tier = next_tier(current_tier)
            if new_tier == current_tier:
                logger.debug("Already at max tier %s — returning result", current_tier.value)
                return result

            logger.debug(
                "Escalating from %s → %s (attempt %d)",
                current_tier.value,
                new_tier.value,
                attempt + 1,
            )
            current_tier = new_tier
            current_intent = dataclasses.replace(current_intent, model_tier=new_tier)

        return result  # type: ignore[return-value]  # exhausted
