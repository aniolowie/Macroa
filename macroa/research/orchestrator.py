"""Research Orchestrator — coordinates the four-phase multi-agent research pipeline.

Pipeline:
  Phase 1  ORCHESTRATE  — SONNET decomposes query into 3–5 trajectories
  Phase 2  INVESTIGATE  — HAIKU subagents run web_search + fetch_url per trajectory
  Phase 3  VERIFY       — HAIKU flags low-confidence claims across all findings
  Phase 4  SYNTHESIZE   — SONNET combines everything into a cited markdown report
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from macroa.kernel.events import Event, Events, bus
from macroa.research.subagent import SubagentResult, SubagentRunner
from macroa.research.synthesizer import synthesize, verify
from macroa.stdlib.schema import DriverBundle, ModelTier

logger = logging.getLogger(__name__)

_PLAN_SYSTEM = """\
You are the Lead Research Orchestrator. Decompose the user's query into 3–5
independent, non-overlapping investigation trajectories. Each trajectory
explores a distinct aspect of the topic.

Return ONLY a valid JSON array — no other text, no code fences:
[
  {
    "id": "short_snake_case_id",
    "objective": "One specific, answerable question this trajectory must resolve",
    "search_seeds": ["concrete web search query 1", "concrete web search query 2"]
  }
]

Rules:
- Trajectories must be non-overlapping (different angles, not the same search)
- Objectives must be specific and directly answerable from web sources
- search_seeds must be concrete queries a search engine would understand
- Use 3 trajectories for focused queries, up to 5 for broad ones
"""


@dataclass
class Trajectory:
    id: str
    objective: str
    search_seeds: list[str] = field(default_factory=list)


class ResearchOrchestrator:
    """Runs the full four-phase research pipeline and returns a report."""

    def __init__(self, drivers: DriverBundle) -> None:
        self._drivers = drivers

    def run(self, query: str) -> tuple[str, list[str]]:
        """Execute all four phases. Returns (report_markdown, citation_urls)."""
        def _phase(n: int, name: str, description: str) -> None:
            logger.info("Research Phase %d — %s", n, name.lower())
            bus.emit(Event(
                event_type=Events.RESEARCH_PHASE_START,
                source="research.orchestrator",
                payload={"phase": n, "name": name, "description": description, "query": query},
            ))

        _phase(1, "Planning", "Decomposing query into investigation trajectories")
        trajectories = self._plan(query)

        _phase(2, "Investigating", f"{len(trajectories)} subagents searching in parallel")
        results = self._investigate(trajectories)

        _phase(3, "Verifying", "Cross-checking findings for confidence")
        verified = verify(query, results, self._drivers)

        _phase(4, "Synthesising", "Combining verified findings into a report")
        report = synthesize(query, verified, self._drivers)

        return report, verified.all_citations

    # ── Phase 1 ───────────────────────────────────────────────────────────────

    def _plan(self, query: str) -> list[Trajectory]:
        messages = [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": query},
        ]
        try:
            raw = self._drivers.llm.complete(
                messages=messages,
                tier=ModelTier.SONNET,
                expect_json=True,
                temperature=0.0,
            )
            raw = _strip_fences(raw)
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("expected JSON array")
            return [
                Trajectory(
                    id=str(t.get("id", f"t{i}")),
                    objective=str(t.get("objective", query)),
                    search_seeds=list(t.get("search_seeds", [])),
                )
                for i, t in enumerate(data[:5])  # cap at 5 trajectories
            ]
        except Exception as exc:
            logger.warning("Planning LLM failed (%s) — single trajectory fallback", exc)
            return [Trajectory(id="main", objective=query, search_seeds=[query])]

    # ── Phase 2 ───────────────────────────────────────────────────────────────

    def _investigate(self, trajectories: list[Trajectory]) -> list[SubagentResult]:
        runner = SubagentRunner(self._drivers)
        total = len(trajectories)
        results: list[SubagentResult] = []
        for i, traj in enumerate(trajectories):
            bus.emit(Event(
                event_type=Events.RESEARCH_SUBAGENT_START,
                source="research.orchestrator",
                payload={"subagent_n": i + 1, "total": total, "objective": traj.objective},
            ))
            result = runner.run(
                n=i + 1,
                trajectory_id=traj.id,
                objective=traj.objective,
                total=total,
            )
            results.append(result)
        return results


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()
