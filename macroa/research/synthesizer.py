"""Verification (Phase 3) and synthesis (Phase 4) for the research pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from macroa.research.subagent import SubagentResult
from macroa.stdlib.schema import DriverBundle, ModelTier

logger = logging.getLogger(__name__)

_VERIFY_SYSTEM = """\
You are a Research Verifier. Review the following subagent findings for quality.

For each factual claim, silently assess confidence:
  HIGH    — supported by multiple independent sources
  MEDIUM  — from a single source
  LOW     — obscure, unsourced, or contradicted — append "(low confidence)"

Output:
1. A 1–2 sentence overall verification note (e.g. "Findings are well-sourced except…")
2. Each subagent's findings restated with LOW-confidence claims marked inline.

Be concise. Do not add new information.
"""

_SYNTHESIZE_SYSTEM = """\
You are the Lead Research Synthesizer. Combine verified subagent findings into \
one comprehensive markdown report.

Requirements:
- ## section headers, one per investigation trajectory
- Every factual claim has an inline citation: [Source N]
- ## Sources section at the end: numbered list of all URLs
- Low-confidence claims are marked *(low confidence)*
- ## Summary at the end: 3–5 bullet points of the most important findings
- Do not include claims beyond what subagents found
- Do not pad — prioritise evidence density
"""


@dataclass
class VerifiedFindings:
    subagent_results: list[SubagentResult]
    verification_notes: str
    all_citations: list[str] = field(default_factory=list)


def verify(query: str, results: list[SubagentResult], drivers: DriverBundle) -> VerifiedFindings:
    """Phase 3 — HAIKU verification pass across all subagent summaries."""
    findings_text = "\n\n".join(
        f"### Subagent {i + 1}: {r.objective}\n{r.findings}"
        for i, r in enumerate(results)
    )
    messages = [
        {"role": "system", "content": _VERIFY_SYSTEM},
        {"role": "user", "content": f"Research query: {query}\n\n{findings_text}"},
    ]
    try:
        notes = drivers.llm.complete(
            messages=messages, tier=ModelTier.HAIKU, temperature=0.0
        )
    except Exception as exc:
        logger.warning("Verification pass failed: %s", exc)
        notes = "[Verification unavailable]"

    # Deduplicate citations across all subagents
    seen: set[str] = set()
    all_urls: list[str] = []
    for r in results:
        for url in r.citations:
            if url not in seen:
                all_urls.append(url)
                seen.add(url)

    return VerifiedFindings(
        subagent_results=results,
        verification_notes=notes,
        all_citations=all_urls,
    )


def synthesize(query: str, verified: VerifiedFindings, drivers: DriverBundle) -> str:
    """Phase 4 — SONNET synthesis into a final markdown report."""
    citations_block = "\n".join(
        f"{i + 1}. {url}" for i, url in enumerate(verified.all_citations)
    )
    findings_block = "\n\n".join(
        f"**Trajectory {i + 1} — {r.objective}**\n{r.findings}"
        for i, r in enumerate(verified.subagent_results)
    )
    user_content = (
        f"Research query: {query}\n\n"
        f"## Verification Notes\n{verified.verification_notes}\n\n"
        f"## Subagent Findings\n{findings_block}\n\n"
        f"## Available Sources\n{citations_block or '(none retrieved)'}"
    )
    messages = [
        {"role": "system", "content": _SYNTHESIZE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    try:
        return drivers.llm.complete(
            messages=messages, tier=ModelTier.SONNET, temperature=0.2
        )
    except Exception as exc:
        logger.error("Synthesis failed: %s", exc, exc_info=True)
        # Graceful degradation — return raw verified findings
        return (
            f"# Research: {query}\n\n"
            f"{findings_block}\n\n"
            f"## Sources\n{citations_block}"
        )
