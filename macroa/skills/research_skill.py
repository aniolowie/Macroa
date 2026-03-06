"""Research skill — multi-agent web research pipeline."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from macroa.research.orchestrator import ResearchOrchestrator
from macroa.stdlib.schema import (
    Context,
    DriverBundle,
    Intent,
    SkillManifest,
    SkillResult,
)

MANIFEST = SkillManifest(
    name="research_skill",
    description=(
        "Multi-agent web research: decomposes the query into trajectories, "
        "searches the web from multiple angles, verifies findings, and produces "
        "a citation-rich markdown report saved to ~/.macroa/research/. "
        "Use when the user asks to research, investigate, find out about, compile "
        "a report on, or look into a topic requiring multiple web sources."
    ),
    triggers=[
        "research",
        "investigate",
        "find out",
        "look into",
        "write a report",
        "compile a report",
        "summarize sources",
        "sources on",
        "report on",
    ],
    model_tier=None,
    deterministic=False,
)

_RESEARCH_DIR = Path.home() / ".macroa" / "research"


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    query = intent.parameters.get("query", intent.raw).strip()

    orchestrator = ResearchOrchestrator(drivers)
    report, citations = orchestrator.run(query)

    # Persist report to ~/.macroa/research/<timestamp>-<slug>.md
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower())[:60].strip("-")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{slug}.md"
    _RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _RESEARCH_DIR / filename
    report_path.write_text(report, encoding="utf-8")

    footer = f"\n\n---\n*Report saved to `~/.macroa/research/{filename}`*"

    return SkillResult(
        output=report + footer,
        success=True,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={
            "skill": "research_skill",
            "citations": citations,
            "report_path": str(report_path),
            "citation_count": len(citations),
        },
    )
