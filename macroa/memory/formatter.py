"""MemoryFormatter — converts retrieved fact dicts into a prompt-ready text block.

The output is never stored to disk. It is generated ephemerally before each
LLM call and injected into the system prompt. The database (SQLite) remains
the single source of truth.
"""

from __future__ import annotations


def format_for_prompt(facts: list[dict]) -> str:
    """Render a list of fact dicts as a concise, LLM-readable memory block.

    Returns an empty string when facts is empty so callers can do a simple
    truthiness check before appending to the system prompt.

    Output example:
        ## What I know about you
        - **name**: Maciej
        - **occupation**: software engineer
        - **primary_language**: Python

        ### Also relevant
        - **current_project**: Macroa v3 memory redesign _(~85% confident)_
    """
    if not facts:
        return ""

    pinned = [f for f in facts if f.get("pinned")]
    contextual = [f for f in facts if not f.get("pinned")]

    sections: list[str] = []

    if pinned:
        lines = ["## What I know about you"]
        for f in pinned:
            lines.append(_render_fact(f))
        sections.append("\n".join(lines))

    if contextual:
        lines = ["### Also relevant"]
        for f in contextual:
            lines.append(_render_fact(f))
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _render_fact(f: dict) -> str:
    line = f"- **{f['key']}**: {f['value']}"
    confidence = f.get("confidence", 1.0)
    if confidence < 0.8:
        line += f" _(~{confidence:.0%} confident)_"
    return line
