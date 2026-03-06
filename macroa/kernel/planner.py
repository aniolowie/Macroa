"""Task planner — decomposes complex requests into typed, tier-assigned subtasks.

Token efficiency principle:
  SONNET/OPUS are only invoked for subtasks that genuinely require P-cores or GPU.
  Planning itself always runs on NANO. Combining always runs on HAIKU.
  Most simple requests skip this layer entirely via heuristic pre-filter.

Flow for a complex request ("do my homework on quantum computing"):
  1. Planner(NANO) → [
       PlanStep("Research key facts",         tier=NANO),
       PlanStep("Write introduction",         tier=HAIKU),
       PlanStep("Write body paragraphs",      tier=SONNET),
       PlanStep("Write conclusion/format",    tier=HAIKU),
     ]
  2. Each step dispatched to chat_skill with prior results injected as context.
  3. Combiner(HAIKU) assembles the final response.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from macroa.config.skill_registry import SkillRegistry
from macroa.drivers.llm_driver import LLMDriver, LLMDriverError
from macroa.stdlib.schema import Context, ModelTier
from macroa.stdlib.text import is_shell_prefix

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ data


@dataclass
class PlanStep:
    subtask: str        # natural-language sub-request (sent as raw_input to chat_skill)
    model_tier: ModelTier


@dataclass
class Plan:
    steps: list[PlanStep]
    combine_tier: ModelTier = ModelTier.HAIKU  # synthesis always on E-cores


# ------------------------------------------------------------------ prompts

_PLAN_SYSTEM = """\
You are a task planner for Macroa, a personal AI OS.

Decide if the user's request should be split into subtasks for efficiency.

ATOMIC (is_complex=false) — handle in a single pass:
- Single-intent: run a command, store a fact, answer one question, write one thing
- Requests under ~2 sentences with a clear single goal

COMPLEX (is_complex=true) — decompose when the request spans distinct phases:
- Research + writing  (e.g. "do my homework on X")
- Analysis + report   (e.g. "analyze this data and write a summary")
- Multi-file setup    (e.g. "scaffold a Python project with tests and CI")
- Sequential dependencies where step N needs step N-1's output

Return ONLY valid JSON, no markdown:

Atomic:
{{"is_complex": false, "steps": []}}

Complex:
{{
  "is_complex": true,
  "steps": [
    {{"subtask": "<exact sub-request>", "tier": "<nano|haiku|sonnet|opus>"}}
  ]
}}

TIER ASSIGNMENT GUIDE (assign cheapest tier that can handle the subtask):
  nano   — fact lookup, data retrieval, trivial classification
  haiku  — summarization, light editing, simple writing, formatting
  sonnet — complex writing, multi-step reasoning, in-depth analysis
  opus   — maximum creativity, synthesis requiring deepest reasoning (use rarely)

Bias toward haiku for writing. Use sonnet only if the subtask genuinely demands it.
Limit to 2–5 steps. More steps = more overhead, so keep it tight.
"""


_COMBINE_SYSTEM = """\
You are assembling subtask results into a single coherent response for the user.
Write as if you produced the whole thing in one pass — do not mention subtasks or steps.
Preserve all key information. Be concise and well-structured.
"""


# ------------------------------------------------------------------ heuristic

_TRIVIAL_PREFIXES = (
    "remember ",
    "forget ",
    "what is my ",
    "what's my ",
    "whats my ",
    "read file ",
    "write file ",
    "show me ",
)


def _is_trivially_atomic(text: str) -> bool:
    """Fast heuristic — skip the planner LLM call for obviously simple inputs."""
    stripped = text.strip()
    if is_shell_prefix(stripped):
        return True
    if len(stripped) < 80:
        return True
    low = stripped.lower()
    return any(low.startswith(p) for p in _TRIVIAL_PREFIXES)


# ------------------------------------------------------------------ planner


class Planner:
    def __init__(self, llm: LLMDriver) -> None:
        self._llm = llm

    def plan(self, raw_input: str, context: Context, registry: SkillRegistry) -> Plan | None:
        """Return a Plan for complex tasks, None for atomic tasks (use existing path)."""
        if _is_trivially_atomic(raw_input):
            return None

        messages = [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": raw_input},
        ]

        try:
            raw_json = self._llm.complete(
                messages=messages,
                tier=ModelTier.NANO,
                expect_json=True,
                temperature=0.0,
            )
            parsed = json.loads(raw_json)

            if not parsed.get("is_complex", False):
                return None

            steps: list[PlanStep] = []
            for s in parsed.get("steps", []):
                tier_str = s.get("tier", "haiku")
                try:
                    tier = ModelTier(tier_str)
                except ValueError:
                    tier = ModelTier.HAIKU
                subtask = s.get("subtask", "").strip()
                if subtask:
                    steps.append(PlanStep(subtask=subtask, model_tier=tier))

            if len(steps) < 2:
                return None  # single step = not worth planning overhead

            logger.debug("Plan: %d steps for %r", len(steps), raw_input[:60])
            return Plan(steps=steps)

        except (json.JSONDecodeError, LLMDriverError, KeyError, TypeError) as exc:
            logger.debug("Planner failed (%s) — treating as atomic", exc)
            return None

    def combine(
        self,
        original_input: str,
        step_results: list[tuple[str, str]],  # (subtask_description, output)
        tier: ModelTier,
    ) -> str:
        """Synthesize N subtask outputs into one coherent response."""
        sections = "\n\n".join(
            f"[{subtask}]\n{output}" for subtask, output in step_results
        )
        messages = [
            {"role": "system", "content": _COMBINE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Original request: {original_input}\n\n"
                    f"Subtask results:\n{sections}"
                ),
            },
        ]
        try:
            return self._llm.complete(messages=messages, tier=tier)
        except LLMDriverError as exc:
            logger.warning("Combiner failed (%s) — joining results directly", exc)
            # Graceful fallback: concatenate without LLM
            return "\n\n---\n\n".join(output for _, output in step_results)
