"""Research Subagent — focused web investigation for a single trajectory."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from macroa.kernel.events import Event, Events, bus
from macroa.stdlib.schema import DriverBundle, ModelTier

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 6  # per subagent — search + fetch + compress fits in 6

# Subagents get web tools only — no shell/file/memory write access
_SUBAGENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for information. Be specific — include entity names, "
                "dates, or qualifiers to get focused results."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and read the text content of a web page.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
]

_SYSTEM_TEMPLATE = """\
You are Research Subagent {n}. Your sole objective: {objective}

Before each tool call, reason through:
  Observe: what information do I have so far?
  Orient:  what gap prevents me from answering the objective?
  Decide:  which search query or URL fills that gap best?

When you have gathered enough evidence — or after exhausting useful sources —
output ONLY the following XML block and nothing after it:

<findings>
Concise, evidence-dense summary (200 words max) that directly answers:
  {objective}
Include specific facts, numbers, and names. If evidence is thin, say so honestly.
</findings>
<citations>
One URL per line — only pages you actually fetched or strongly referenced
</citations>

You have {max_rounds} rounds of tool calls. Start with the most targeted query.
"""


@dataclass
class SubagentResult:
    trajectory_id: str
    objective: str
    findings: str
    citations: list[str] = field(default_factory=list)
    rounds_used: int = 0


def _extract_xml(tag: str, text: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


class SubagentRunner:
    def __init__(self, drivers: DriverBundle) -> None:
        self._drivers = drivers

    def run(self, n: int, trajectory_id: str, objective: str, total: int = 1) -> SubagentResult:
        # Import executors here to avoid circular imports
        from macroa.kernel.tool_defs import _fetch_url, _web_search  # type: ignore[attr-defined]

        system = _SYSTEM_TEMPLATE.format(
            n=n, objective=objective, max_rounds=_MAX_ROUNDS
        )
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Begin researching: {objective}"},
        ]

        rounds = 0
        final_content = ""

        try:
            while rounds < _MAX_ROUNDS:
                content, tool_calls = self._drivers.llm.complete_with_tools(
                    messages=messages,
                    tools=_SUBAGENT_TOOLS,
                    tier=ModelTier.HAIKU,
                )
                final_content = content or ""

                if not tool_calls:
                    break  # LLM finished — extract findings

                messages.append({
                    "role": "assistant",
                    "content": content or "",
                    "tool_calls": [tc.model_dump() for tc in tool_calls],
                })

                for call in tool_calls:
                    try:
                        args = json.loads(call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    name = call.function.name
                    logger.debug("Subagent %d tool: %s(%s)", n, name, list(args.keys()))

                    if name == "web_search":
                        arg = args.get("query", "")
                        result = _web_search(arg, self._drivers)
                    elif name == "fetch_url":
                        arg = args.get("url", "")
                        result = _fetch_url(arg, self._drivers)
                    else:
                        arg = ""
                        result = f"[unknown tool: {name!r}]"

                    bus.emit(Event(
                        event_type=Events.RESEARCH_TOOL_CALL,
                        source="research.subagent",
                        payload={"subagent_n": n, "total": total, "tool": name, "arg": arg},
                    ))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result,
                    })

                rounds += 1

        except Exception as exc:
            logger.error("Subagent %d error: %s", n, exc, exc_info=True)
            bus.emit(Event(
                event_type=Events.RESEARCH_SUBAGENT_DONE,
                source="research.subagent",
                payload={"subagent_n": n, "total": total, "citation_count": 0},
            ))
            return SubagentResult(
                trajectory_id=trajectory_id,
                objective=objective,
                findings=f"[Subagent failed: {exc}]",
                rounds_used=rounds,
            )

        # If we hit max rounds without a clean stop, force a final summarise call
        if rounds >= _MAX_ROUNDS and not re.search(r"<findings>", final_content, re.IGNORECASE):
            summarise_msgs = messages + [{"role": "user", "content": (
                "You have reached your tool call limit. "
                "Summarise everything you found right now:\n"
                "<findings>evidence-dense summary</findings>\n"
                "<citations>one URL per line</citations>"
            )}]
            try:
                forced = self._drivers.llm.complete(
                    messages=summarise_msgs,
                    tier=ModelTier.HAIKU,
                    temperature=0.0,
                )
                if forced:
                    final_content = forced
            except Exception as exc:
                logger.debug("Forced summarise call failed: %s", exc)

        findings = _extract_xml("findings", final_content) or final_content
        citations_raw = _extract_xml("citations", final_content)
        citations = [
            ln.strip()
            for ln in citations_raw.splitlines()
            if ln.strip().startswith("http")
        ]

        bus.emit(Event(
            event_type=Events.RESEARCH_SUBAGENT_DONE,
            source="research.subagent",
            payload={"subagent_n": n, "total": total, "citation_count": len(citations)},
        ))

        return SubagentResult(
            trajectory_id=trajectory_id,
            objective=objective,
            findings=findings,
            citations=citations,
            rounds_used=rounds,
        )
