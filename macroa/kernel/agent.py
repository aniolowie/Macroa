"""AgentLoop — LLM tool-call loop that runs until the model stops invoking tools."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from macroa.drivers.llm_driver import LLMDriverError
from macroa.kernel.clock import now_context
from macroa.kernel.identity import build_system_prompt
from macroa.kernel.tool_defs import TOOL_SCHEMAS, execute_tool
from macroa.memory.retriever import retrieve
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
        base_prompt = build_system_prompt()

        # Inject current time
        time_ctx = now_context(self._drivers.memory)

        # Inject relevant memory facts
        memory_lines: list[str] = []
        try:
            facts = retrieve(intent.raw, self._drivers.memory)
            if facts:
                memory_lines.append("\n## Relevant Memory\n")
                memory_lines.extend(f"- {f['key']}: {f['value']}" for f in facts)
        except Exception:
            pass  # memory retrieval is best-effort — never block agent execution

        # Inject compacted episodes
        episode_lines: list[str] = []
        try:
            episodes = self._drivers.memory.get_episodes(context.session_id, limit=4)
            if episodes:
                episode_lines.append("\n## Earlier in this conversation (compacted)\n")
                episode_lines.extend(f"- {ep.summary}" for ep in episodes)
        except Exception:
            pass  # episode retrieval is best-effort — never block agent execution

        system_prompt = "\n".join(filter(None, [
            time_ctx,
            base_prompt,
            "".join(memory_lines),
            "".join(episode_lines),
        ]))
        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        for entry in context.entries:
            if entry.role in ("user", "assistant"):
                messages.append({"role": entry.role, "content": entry.content})
        messages.append({"role": "user", "content": intent.raw})

        session_id = context.session_id
        budget = self._drivers.budget  # may be None in tests

        tool_rounds = 0
        try:
            while tool_rounds < _MAX_ROUNDS:
                # Check session budget before each LLM call
                if budget and budget.is_over(session_id):
                    return self._budget_exceeded(
                        messages, intent, tool_rounds, budget.stats(session_id)
                    )

                content, tool_calls = self._drivers.llm.complete_with_tools(
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tier=intent.model_tier,
                )

                # Record token usage after each call
                if budget and self._drivers.llm.last_usage:
                    u = self._drivers.llm.last_usage
                    budget.record(
                        session_id,
                        u.get("prompt_tokens", 0),
                        u.get("completion_tokens", 0),
                        u.get("model", ""),
                    )

                if not tool_calls:
                    stats = budget.stats(session_id) if budget else {}
                    return SkillResult(
                        output=content,
                        success=True,
                        turn_id=intent.turn_id,
                        model_tier=intent.model_tier,
                        metadata={
                            "skill": "agent_skill",
                            "tier": intent.model_tier.value,
                            "tool_rounds": tool_rounds,
                            **({} if not stats else {"budget": stats}),
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

            # Hit round limit — force a graceful summary
            return self._force_summarize(messages, intent, tool_rounds, reason="round limit")

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

    def _force_summarize(
        self, messages: list[dict], intent: Intent, rounds: int, reason: str
    ) -> SkillResult:
        """Ask the LLM to summarise what it has done so far instead of stopping cold."""
        logger.warning("AgentLoop: %s after %d rounds — forcing summarise", reason, rounds)
        summarise_msgs = messages + [{"role": "user", "content": (
            f"You have been stopped ({reason}). "
            "Summarise clearly what you have accomplished so far and what remains to be done. "
            "Be concise and direct."
        )}]
        try:
            summary = self._drivers.llm.complete(
                messages=summarise_msgs,
                tier=intent.model_tier,
                temperature=0.0,
            )
        except Exception as exc:
            summary = f"(summary unavailable: {exc})"

        return SkillResult(
            output=summary,
            success=False,
            error=reason,
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
            metadata={"skill": "agent_skill", "tool_rounds": rounds, "stopped_by": reason},
        )

    def _budget_exceeded(
        self, messages: list[dict], intent: Intent, rounds: int, stats: dict
    ) -> SkillResult:
        spent = stats.get("spent_usd", 0)
        tokens = stats.get("spent_tokens", 0)
        logger.warning(
            "AgentLoop: session budget exceeded — $%.4f / %d tokens after %d rounds",
            spent, tokens, rounds,
        )
        result = self._force_summarize(messages, intent, rounds, reason="session budget exceeded")
        result.metadata["budget"] = stats
        return result
