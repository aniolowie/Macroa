"""Intent router — classifies user input to a skill + parameters."""

from __future__ import annotations

import json
import logging
import re
import uuid

from macroa.config.skill_registry import SkillRegistry
from macroa.drivers.llm_driver import LLMDriver, LLMDriverError
from macroa.kernel.escalation import resolve_tier
from macroa.stdlib.schema import Context, Intent, ModelTier
from macroa.stdlib.text import is_shell_prefix, strip_shell_prefix

logger = logging.getLogger(__name__)

_ROUTING_SYSTEM = """\
You are the intent router for Macroa, a personal AI OS.
Your job is to classify the user's input and return a JSON object.

Available skills:
{skill_descriptions}

Return ONLY valid JSON with this exact schema:
{{
  "skill_name": "<one of the skill names above>",
  "parameters": {{}},
  "confidence": 0.0,
  "reasoning": ""
}}

Rules:
- "parameters" must be a flat JSON object with string/number/bool values.
- For memory_skill, include "action" (set|get|search|delete|list), "key", "value", "query" as appropriate.
- For file_skill, include "action" (read|write|list|exists) and "path", "content" as appropriate.
- For shell_skill, include "command" with the shell command string.
- "confidence" is a float 0.0–1.0.
- Default to chat_skill if no other skill clearly matches.
- Never include markdown, code fences, or explanation outside the JSON.
"""


def _extract_json(text: str) -> str:
    """Strip markdown code fences and whitespace from an LLM response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


class Router:
    def __init__(
        self,
        llm: LLMDriver,
        registry: SkillRegistry,
    ) -> None:
        self._llm = llm
        self._registry = registry

    def route(self, raw_input: str, context: Context) -> Intent:
        turn_id = str(uuid.uuid4())

        # Hard-route shell commands (no LLM)
        if is_shell_prefix(raw_input):
            command = strip_shell_prefix(raw_input)
            manifest = self._registry.get("shell_skill")
            pinned = manifest.manifest.model_tier if manifest else None
            tier = resolve_tier(raw_input, pinned)
            return Intent(
                raw=raw_input,
                skill_name="shell_skill",
                parameters={"command": command},
                model_tier=tier,
                routing_confidence=1.0,
                turn_id=turn_id,
            )

        # LLM routing
        skill_descriptions = self._format_skill_descriptions()
        system_prompt = _ROUTING_SYSTEM.format(skill_descriptions=skill_descriptions)

        # Build a short context slice for the router (last 4 turns)
        recent = context.entries[-8:] if context.entries else []
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for entry in recent:
            if entry.role in ("user", "assistant"):
                messages.append({"role": entry.role, "content": entry.content})
        messages.append({"role": "user", "content": raw_input})

        try:
            raw_json = self._llm.complete(
                messages=messages,
                tier=ModelTier.NANO,  # routing always uses the cheapest tier
                expect_json=True,
                temperature=0.0,
            )
            parsed = json.loads(_extract_json(raw_json))
            skill_name = parsed.get("skill_name", "chat_skill")
            parameters = parsed.get("parameters", {})
            confidence = float(parsed.get("confidence", 0.5))

            # Validate skill exists
            entry = self._registry.get(skill_name)
            if entry is None:
                logger.warning("Router returned unknown skill %r — falling back to chat_skill", skill_name)
                skill_name = "chat_skill"
                parameters = {}
                confidence = 0.3
                entry = self._registry.get("chat_skill")

            pinned = entry.manifest.model_tier if entry else None
            tier = resolve_tier(raw_input, pinned)

            return Intent(
                raw=raw_input,
                skill_name=skill_name,
                parameters=parameters,
                model_tier=tier,
                routing_confidence=confidence,
                turn_id=turn_id,
            )

        except (json.JSONDecodeError, LLMDriverError, KeyError, ValueError) as exc:
            logger.warning("Routing failed (%s) — falling back to chat_skill", exc)
            return Intent(
                raw=raw_input,
                skill_name="chat_skill",
                parameters={},
                model_tier=ModelTier.NANO,
                routing_confidence=0.0,
                turn_id=turn_id,
            )

    def _format_skill_descriptions(self) -> str:
        lines = []
        for manifest in self._registry.all_manifests():
            triggers = ", ".join(manifest.triggers[:5])
            lines.append(
                f"- name: {manifest.name}\n"
                f"  description: {manifest.description}\n"
                f"  example triggers: {triggers}"
            )
        return "\n".join(lines)
