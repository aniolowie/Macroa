"""Intent router — classifies user input to a skill + parameters.

Routing pipeline (in order):
  1. Hard-route shell prefix (! / $) — no LLM, confidence 1.0
  2. Keyword shortcut — if exactly one non-chat skill's trigger matches
     unambiguously, skip the LLM call entirely (confidence 0.95)
  3. NANO LLM call with JSON mode + few-shot examples
  4. If confidence < 0.5 on a non-chat result, retry with HAIKU
  5. Validate skill exists; validate required parameters are present
  6. Fall back to chat_skill on any exception
"""

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

# ── Regex-based memory shortcuts (bypass LLM entirely) ────────────────────────
# "remember/save/store [that] [my] <key> is/are/= <value>"
_MEM_SET_RE = re.compile(
    r"^(?:please\s+)?(?:remember|save|store)\s+"
    r"(?:that\s+)?(?:my\s+)?([a-zA-Z][\w\s\-]{0,50}?)\s+"
    r"(?:is|are|=)\s+"
    r"(.{1,300}?)[\.\!]?\s*$",
    re.IGNORECASE,
)
# "what['s/is] my <key>[?]"
_MEM_GET_RE = re.compile(
    r"^what(?:'s|\s+is)?\s+my\s+([a-zA-Z][\w\s\-]{0,50}?)\s*\??\s*$",
    re.IGNORECASE,
)


def _to_snake(text: str) -> str:
    return re.sub(r"[^\w]+", "_", text.strip().lower()).strip("_")


# Triggers too common/short to use as unambiguous keyword shortcuts
_AMBIGUOUS_TRIGGERS = {
    "chat", "ask", "help", "what", "how", "why", "tell me",
    "cat", "save", "ls", "ps", "pwd", "bash", "shell",
}

# Required parameters per skill + action (best-effort; missing ones log a warning)
_REQUIRED_PARAMS: dict[str, dict[str, list[str]] | list[str]] = {
    "file_skill": {
        "read":   ["path"],
        "write":  ["path", "content"],
        "list":   ["path"],
        "exists": ["path"],
    },
    "memory_skill": {
        "set":    ["key", "value"],
        "get":    ["key"],
        "search": ["query"],
        "delete": ["key"],
    },
    "shell_skill": ["command"],
}

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
- For memory_skill: include "action" (set|get|search|delete|list), plus "key", \
"value", or "query" as needed.
- For file_skill: include "action" (read|write|list|exists), "path", and "content" \
(for write).
- For shell_skill: include "command" with the exact shell command string.
- For agent_skill: parameters can be empty — it handles multi-step tasks with tools.
- "confidence" is a float 0.0–1.0.
- Consider the conversation context shown above when routing — prefer skill \
continuity when the user is mid-task.
- Default to chat_skill only when no other skill clearly applies.
- NEVER include markdown, code fences, or any text outside the JSON object.

Examples:
  Input: "remember my dog is called Rex"
  → {{"skill_name":"memory_skill","parameters":{{"action":"set","key":"dog_name",\
"value":"Rex"}},"confidence":0.97,"reasoning":"explicit store intent"}}

  Input: "what's the capital of France"
  → {{"skill_name":"chat_skill","parameters":{{}},"confidence":0.93,\
"reasoning":"general knowledge question, no action needed"}}

  Input: "read the file /etc/hosts"
  → {{"skill_name":"file_skill","parameters":{{"action":"read","path":"/etc/hosts"}},\
"confidence":0.98,"reasoning":"explicit file read"}}

  Input: "set up my workspace and write my identity files"
  → {{"skill_name":"agent_skill","parameters":{{}},"confidence":0.91,\
"reasoning":"multi-step task requiring file writes and workspace setup"}}

  Input: "show disk usage"
  → {{"skill_name":"shell_skill","parameters":{{"command":"df -h"}},"confidence":0.87,\
"reasoning":"system info best answered via shell"}}

  Input: "research top laners in League of Legends and compile a report"
  → {{"skill_name":"research_skill","parameters":{{"query":"top laners League of Legends competitive"}},"confidence":0.95,\
"reasoning":"multi-source research task requiring web investigation and synthesis"}}
"""


def _extract_json(text: str) -> str:
    """Strip markdown code fences and surrounding whitespace."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _check_params(skill_name: str, parameters: dict) -> None:
    """Log a warning if required parameters are missing (non-fatal)."""
    spec = _REQUIRED_PARAMS.get(skill_name)
    if spec is None:
        return
    if isinstance(spec, list):
        missing = [k for k in spec if k not in parameters]
    else:
        action = parameters.get("action", "")
        required = spec.get(action, [])
        missing = [k for k in required if k not in parameters]
    if missing:
        logger.warning(
            "Router: %r missing parameters %s for action %r",
            skill_name, missing, parameters.get("action", ""),
        )


class Router:
    def __init__(self, llm: LLMDriver, registry: SkillRegistry) -> None:
        self._llm = llm
        self._registry = registry

    def route(self, raw_input: str, context: Context) -> Intent:
        turn_id = str(uuid.uuid4())

        # ── Stage 1: hard-route shell prefix ─────────────────────────────────
        if is_shell_prefix(raw_input):
            command = strip_shell_prefix(raw_input)
            entry = self._registry.get("shell_skill")
            pinned = entry.manifest.model_tier if entry else None
            return Intent(
                raw=raw_input,
                skill_name="shell_skill",
                parameters={"command": command},
                model_tier=resolve_tier(raw_input, pinned),
                routing_confidence=1.0,
                turn_id=turn_id,
            )

        # ── Stage 1.5: regex memory shortcuts (no LLM, no JSON risk) ─────────
        memory_intent = self._memory_shortcut(raw_input, turn_id)
        if memory_intent:
            return memory_intent

        # ── Stage 2: keyword shortcut (no LLM) ───────────────────────────────
        keyword_skill = self._keyword_route(raw_input)
        if keyword_skill:
            entry = self._registry.get(keyword_skill)
            pinned = entry.manifest.model_tier if entry else None
            safe_snippet = raw_input[:40].replace("\r", "\\r").replace("\n", "\\n")
            logger.debug("Router keyword-shortcut: %r → %s", safe_snippet, keyword_skill)
            return Intent(
                raw=raw_input,
                skill_name=keyword_skill,
                parameters={},
                model_tier=resolve_tier(raw_input, pinned),
                routing_confidence=0.95,
                turn_id=turn_id,
            )

        # ── Stage 3: LLM routing ──────────────────────────────────────────────
        skill_descriptions = self._format_skill_descriptions()
        system_prompt = _ROUTING_SYSTEM.format(skill_descriptions=skill_descriptions)

        recent = context.entries[-8:] if context.entries else []
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for entry in recent:
            if entry.role in ("user", "assistant"):
                messages.append({"role": entry.role, "content": entry.content})
        messages.append({"role": "user", "content": raw_input})

        try:
            raw_json = self._llm.complete(
                messages=messages,
                tier=ModelTier.NANO,
                expect_json=True,
                temperature=0.0,
            )
            parsed = json.loads(_extract_json(raw_json))
            skill_name = parsed.get("skill_name", "chat_skill")
            parameters = parsed.get("parameters", {})
            confidence = float(parsed.get("confidence", 0.5))

            # ── Stage 4: low-confidence retry with HAIKU ──────────────────────
            if confidence < 0.5 and skill_name != "chat_skill":
                try:
                    raw_json2 = self._llm.complete(
                        messages=messages,
                        tier=ModelTier.HAIKU,
                        expect_json=True,
                        temperature=0.0,
                    )
                    parsed2 = json.loads(_extract_json(raw_json2))
                    c2 = float(parsed2.get("confidence", 0.0))
                    if c2 > confidence:
                        parsed, skill_name = parsed2, parsed2.get("skill_name", skill_name)
                        parameters = parsed2.get("parameters", parameters)
                        confidence = c2
                        logger.debug("Router HAIKU retry improved confidence %.2f → %.2f", confidence, c2)
                except Exception as retry_exc:
                    logger.debug("Router HAIKU retry failed: %s", retry_exc)

            # ── Stage 5: validate skill + parameters ──────────────────────────
            reg_entry = self._registry.get(skill_name)
            if reg_entry is None:
                logger.warning("Router returned unknown skill %r — falling back to chat_skill", skill_name)
                skill_name, parameters, confidence = "chat_skill", {}, 0.3
                reg_entry = self._registry.get("chat_skill")

            _check_params(skill_name, parameters)

            pinned = reg_entry.manifest.model_tier if reg_entry else None
            return Intent(
                raw=raw_input,
                skill_name=skill_name,
                parameters=parameters,
                model_tier=resolve_tier(raw_input, pinned),
                routing_confidence=confidence,
                turn_id=turn_id,
            )

        except (json.JSONDecodeError, LLMDriverError, KeyError, ValueError) as exc:
            logger.warning("Routing failed (%s) — retrying with HAIKU", exc)
            try:
                raw_json2 = self._llm.complete(
                    messages=messages,
                    tier=ModelTier.HAIKU,
                    expect_json=True,
                    temperature=0.0,
                )
                parsed2 = json.loads(_extract_json(raw_json2))
                skill_name2 = parsed2.get("skill_name", "chat_skill")
                parameters2 = parsed2.get("parameters", {})
                confidence2 = float(parsed2.get("confidence", 0.5))

                reg_entry2 = self._registry.get(skill_name2)
                if reg_entry2 is None:
                    skill_name2, parameters2, confidence2 = "chat_skill", {}, 0.3
                    reg_entry2 = self._registry.get("chat_skill")

                _check_params(skill_name2, parameters2)
                pinned2 = reg_entry2.manifest.model_tier if reg_entry2 else None
                return Intent(
                    raw=raw_input,
                    skill_name=skill_name2,
                    parameters=parameters2,
                    model_tier=resolve_tier(raw_input, pinned2),
                    routing_confidence=confidence2,
                    turn_id=turn_id,
                )
            except Exception as retry_exc:
                logger.warning("HAIKU retry failed (%s) — falling back to chat_skill", retry_exc)
                return Intent(
                    raw=raw_input,
                    skill_name="chat_skill",
                    parameters={},
                    model_tier=ModelTier.NANO,
                    routing_confidence=0.0,
                    turn_id=turn_id,
                )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _memory_shortcut(self, raw_input: str, turn_id: str) -> Intent | None:
        """Regex-based extraction for common memory patterns — no LLM call needed."""
        stripped = raw_input.strip()

        m = _MEM_SET_RE.match(stripped)
        if m:
            key = _to_snake(m.group(1))
            value = m.group(2).strip().strip("'\"")
            if key and value:
                entry = self._registry.get("memory_skill")
                pinned = entry.manifest.model_tier if entry else None
                logger.debug("Router memory-set shortcut: %r → key=%r", stripped[:40], key)
                return Intent(
                    raw=raw_input,
                    skill_name="memory_skill",
                    parameters={"action": "set", "key": key, "value": value},
                    model_tier=resolve_tier(raw_input, pinned),
                    routing_confidence=0.97,
                    turn_id=turn_id,
                )

        m = _MEM_GET_RE.match(stripped)
        if m:
            query = m.group(1).strip()
            if query:
                entry = self._registry.get("memory_skill")
                pinned = entry.manifest.model_tier if entry else None
                logger.debug("Router memory-get shortcut: %r → query=%r", stripped[:40], query)
                return Intent(
                    raw=raw_input,
                    skill_name="memory_skill",
                    parameters={"action": "search", "query": query},
                    model_tier=resolve_tier(raw_input, pinned),
                    routing_confidence=0.92,
                    turn_id=turn_id,
                )

        return None

    def _keyword_route(self, raw_input: str) -> str | None:
        """Return a skill name if exactly one non-chat skill has an unambiguous
        trigger match. Returns None to let the LLM decide.

        Skills that require parameter extraction are excluded so the LLM can
        extract key/value/action instead of defaulting to empty parameters.
        """
        _NO_KEYWORD_ROUTE = {"chat_skill", "memory_skill"}
        lower = raw_input.lower()
        matched: set[str] = set()
        for manifest in self._registry.all_manifests():
            if manifest.name in _NO_KEYWORD_ROUTE:
                continue
            for trigger in manifest.triggers:
                if (
                    len(trigger) >= 5
                    and trigger not in _AMBIGUOUS_TRIGGERS
                    and trigger.lower() in lower
                ):
                    matched.add(manifest.name)
                    break
        return matched.pop() if len(matched) == 1 else None

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
