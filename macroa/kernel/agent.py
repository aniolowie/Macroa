"""AgentLoop — LLM tool-call loop that runs until the model stops invoking tools."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from macroa.drivers.llm_driver import LLMDriverError
from macroa.kernel.identity import build_system_prompt
from macroa.kernel.tool_defs import TOOL_SCHEMAS, execute_tool
from macroa.stdlib.schema import Context, DriverBundle, Intent, SkillResult

logger = logging.getLogger(__name__)

ConfirmCallback = Callable[[str, str], bool]

_MAX_ROUNDS = 20  # safety cap — allows multi-source research without premature cutoff


class AgentLoop:
    """Runs the LLM in a tool-calling loop.

    Each round:
      1. Call LLM with current messages + tool schemas
      2. If response contains tool_calls: execute each, append results, continue
      3. If response has no tool_calls: return final text as SkillResult
    """

    def __init__(
        self,
        drivers: DriverBundle,
        confirm_callback: ConfirmCallback | None,
        session_approved: set[str],
    ) -> None:
        self._drivers = drivers
        self._confirm = confirm_callback
        self._approved = session_approved

    def run(self, intent: Intent, context: Context) -> SkillResult:
        system_prompt = build_system_prompt()
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        for entry in context.entries:
            if entry.role in ("user", "assistant"):
                messages.append({"role": entry.role, "content": entry.content})
        messages.append({"role": "user", "content": intent.raw})

        tool_rounds = 0
        try:
            while tool_rounds < _MAX_ROUNDS:
                content, tool_calls = self._drivers.llm.complete_with_tools(
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tier=intent.model_tier,
                )

                if not tool_calls:
                    return SkillResult(
                        output=content,
                        success=True,
                        turn_id=intent.turn_id,
                        model_tier=intent.model_tier,
                        metadata={
                            "skill": "agent_skill",
                            "tier": intent.model_tier.value,
                            "tool_rounds": tool_rounds,
                        },
                    )

                # Append assistant message with tool calls
                messages.append({
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": [tc.model_dump() for tc in tool_calls],
                })

                # Execute each tool and append results
                for call in tool_calls:
                    try:
                        args = json.loads(call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    logger.debug("Agent tool: %s(%s)", call.function.name, list(args.keys()))

                    result_str = execute_tool(
                        name=call.function.name,
                        args=args,
                        drivers=self._drivers,
                        session_approved=self._approved,
                        confirm_callback=self._confirm,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result_str,
                    })

                tool_rounds += 1

            return SkillResult(
                output="I hit my tool call limit before finishing. Please ask me to continue.",
                success=False,
                error="Tool call limit exceeded",
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"skill": "agent_skill", "tool_rounds": tool_rounds},
            )

        except LLMDriverError as exc:
            return SkillResult(
                output="",
                success=False,
                error=f"agent_skill LLM error: {exc}",
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
            )
        except Exception as exc:
            logger.error("AgentLoop unexpected error: %s", exc, exc_info=True)
            return SkillResult(
                output="",
                success=False,
                error=f"agent_skill error: {exc}",
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
            )
