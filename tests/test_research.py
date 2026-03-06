"""Tests for the multi-agent research module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from macroa.research.orchestrator import ResearchOrchestrator, _strip_fences
from macroa.research.subagent import SubagentRunner, _extract_xml
from macroa.research.synthesizer import VerifiedFindings, synthesize, verify
from macroa.stdlib.schema import DriverBundle, ModelTier

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_drivers(
    complete_returns: str = "",
    complete_with_tools_returns: tuple = ("", []),
) -> DriverBundle:
    llm = MagicMock()
    llm.complete.return_value = complete_returns
    llm.complete_with_tools.return_value = complete_with_tools_returns
    return DriverBundle(
        llm=llm,
        shell=MagicMock(),
        fs=MagicMock(),
        memory=MagicMock(),
        network=MagicMock(),
    )


# ── _strip_fences ─────────────────────────────────────────────────────────────


def test_strip_fences_plain():
    assert _strip_fences('  [{"id":"a"}]  ') == '[{"id":"a"}]'


def test_strip_fences_json_block():
    assert _strip_fences('```json\n[{"id":"a"}]\n```') == '[{"id":"a"}]'


def test_strip_fences_plain_block():
    assert _strip_fences('```\n[1,2]\n```') == "[1,2]"


# ── _extract_xml ──────────────────────────────────────────────────────────────


def test_extract_xml_found():
    text = "blah\n<findings>\nsome facts\n</findings>\nmore"
    assert _extract_xml("findings", text) == "some facts"


def test_extract_xml_missing_returns_empty():
    assert _extract_xml("findings", "no xml here") == ""


def test_extract_xml_case_insensitive():
    assert _extract_xml("FINDINGS", "<findings>data</findings>") == "data"


# ── ResearchOrchestrator._plan ────────────────────────────────────────────────


def test_plan_parses_valid_json():
    payload = json.dumps([
        {"id": "bg", "objective": "Background on X", "search_seeds": ["X history"]},
        {"id": "stats", "objective": "Statistics for X", "search_seeds": ["X stats 2024"]},
    ])
    drivers = _make_drivers(complete_returns=payload)
    orch = ResearchOrchestrator(drivers)
    trajectories = orch._plan("Tell me about X")
    assert len(trajectories) == 2
    assert trajectories[0].id == "bg"
    assert trajectories[1].objective == "Statistics for X"


def test_plan_falls_back_on_invalid_json():
    drivers = _make_drivers(complete_returns="not json at all")
    orch = ResearchOrchestrator(drivers)
    trajectories = orch._plan("my query")
    assert len(trajectories) == 1
    assert trajectories[0].id == "main"
    assert trajectories[0].objective == "my query"


def test_plan_falls_back_on_non_list():
    drivers = _make_drivers(complete_returns='{"id":"x"}')
    orch = ResearchOrchestrator(drivers)
    trajectories = orch._plan("query")
    assert len(trajectories) == 1


def test_plan_caps_at_five_trajectories():
    payload = json.dumps([
        {"id": f"t{i}", "objective": f"obj {i}", "search_seeds": []} for i in range(10)
    ])
    drivers = _make_drivers(complete_returns=payload)
    orch = ResearchOrchestrator(drivers)
    assert len(orch._plan("broad query")) == 5


# ── SubagentRunner ────────────────────────────────────────────────────────────


def test_subagent_returns_findings_when_no_tools():
    final = (
        "Some content\n"
        "<findings>\nLeague esports is popular.\n</findings>\n"
        "<citations>\nhttps://example.com/esports\n</citations>"
    )
    drivers = _make_drivers(complete_with_tools_returns=(final, []))
    runner = SubagentRunner(drivers)
    result = runner.run(n=1, trajectory_id="bg", objective="League of Legends esports")
    assert result.findings == "League esports is popular."
    assert result.citations == ["https://example.com/esports"]
    assert result.rounds_used == 0


def test_subagent_fallback_when_no_findings_tag():
    long_text = "A" * 700
    drivers = _make_drivers(complete_with_tools_returns=(long_text, []))
    runner = SubagentRunner(drivers)
    result = runner.run(n=1, trajectory_id="t", objective="obj")
    # Falls back to full content (no truncation)
    assert result.findings == long_text


def test_subagent_filters_non_http_citations():
    final = (
        "<findings>facts</findings>"
        "<citations>\nhttps://good.com\nnot a url\nhttps://also-good.com\n</citations>"
    )
    drivers = _make_drivers(complete_with_tools_returns=(final, []))
    runner = SubagentRunner(drivers)
    result = runner.run(n=1, trajectory_id="t", objective="obj")
    assert result.citations == ["https://good.com", "https://also-good.com"]


def test_subagent_handles_llm_error():
    llm = MagicMock()
    llm.complete_with_tools.side_effect = RuntimeError("API down")
    drivers = DriverBundle(
        llm=llm,
        shell=MagicMock(),
        fs=MagicMock(),
        memory=MagicMock(),
        network=MagicMock(),
    )
    runner = SubagentRunner(drivers)
    result = runner.run(n=1, trajectory_id="t", objective="obj")
    assert not result.findings.startswith("League")  # error message
    assert "failed" in result.findings.lower()


# ── synthesizer.verify ────────────────────────────────────────────────────────


def test_verify_calls_llm_and_deduplicates_citations():
    from macroa.research.subagent import SubagentResult

    results = [
        SubagentResult("t1", "obj1", "fact A", citations=["https://a.com", "https://b.com"]),
        SubagentResult("t2", "obj2", "fact B", citations=["https://b.com", "https://c.com"]),
    ]
    drivers = _make_drivers(complete_returns="All findings are HIGH confidence.")
    verified = verify("test query", results, drivers)
    assert verified.verification_notes == "All findings are HIGH confidence."
    assert verified.all_citations == ["https://a.com", "https://b.com", "https://c.com"]


def test_verify_survives_llm_error():
    from macroa.research.subagent import SubagentResult

    llm = MagicMock()
    llm.complete.side_effect = RuntimeError("LLM gone")
    drivers = DriverBundle(
        llm=llm, shell=MagicMock(), fs=MagicMock(), memory=MagicMock(), network=MagicMock()
    )
    verified = verify("q", [SubagentResult("t", "o", "f")], drivers)
    assert "unavailable" in verified.verification_notes.lower()


# ── synthesizer.synthesize ────────────────────────────────────────────────────


def test_synthesize_returns_llm_output():
    from macroa.research.subagent import SubagentResult

    verified = VerifiedFindings(
        subagent_results=[SubagentResult("t", "obj", "findings text")],
        verification_notes="ok",
        all_citations=["https://src.com"],
    )
    drivers = _make_drivers(complete_returns="# Final Report\n\nGreat stuff.")
    report = synthesize("query", verified, drivers)
    assert report == "# Final Report\n\nGreat stuff."


def test_synthesize_fallback_on_error():
    from macroa.research.subagent import SubagentResult

    llm = MagicMock()
    llm.complete.side_effect = RuntimeError("broken")
    drivers = DriverBundle(
        llm=llm, shell=MagicMock(), fs=MagicMock(), memory=MagicMock(), network=MagicMock()
    )
    verified = VerifiedFindings(
        subagent_results=[SubagentResult("t", "objective X", "some findings")],
        verification_notes="ok",
        all_citations=[],
    )
    report = synthesize("query", verified, drivers)
    assert "some findings" in report


# ── ResearchOrchestrator.run (integration, all mocked) ───────────────────────


def test_orchestrator_run_end_to_end(tmp_path: Path):
    plan_json = json.dumps([
        {"id": "bg", "objective": "background", "search_seeds": ["bg search"]},
    ])
    # complete() is called for plan, verify, synthesize
    complete_responses = [plan_json, "HIGH confidence.", "# Report\nFinal."]
    complete_calls = iter(complete_responses)

    llm = MagicMock()
    llm.complete.side_effect = lambda **kwargs: next(complete_calls)
    llm.complete_with_tools.return_value = (
        "<findings>key fact</findings><citations>https://s.com</citations>",
        [],
    )
    drivers = DriverBundle(
        llm=llm, shell=MagicMock(), fs=MagicMock(), memory=MagicMock(), network=MagicMock()
    )
    orch = ResearchOrchestrator(drivers)
    report, citations = orch.run("test query")
    assert report == "# Report\nFinal."
    assert "https://s.com" in citations


# ── research_skill ────────────────────────────────────────────────────────────


def test_research_skill_saves_file(tmp_path: Path):
    from macroa.skills.research_skill import run
    from macroa.stdlib.schema import Intent

    intent = Intent(
        raw="research League of Legends top laners",
        skill_name="research_skill",
        parameters={"query": "League of Legends top laners"},
        model_tier=ModelTier.SONNET,
        routing_confidence=0.95,
        turn_id="test-turn",
    )
    context = MagicMock()
    context.entries = []

    with patch("macroa.skills.research_skill._RESEARCH_DIR", tmp_path), \
         patch("macroa.skills.research_skill.ResearchOrchestrator") as MockOrch:
        MockOrch.return_value.run.return_value = ("# Report", ["https://wiki.com"])
        result = run(intent, context, MagicMock())

    assert result.success
    assert "# Report" in result.output
    assert result.metadata["citation_count"] == 1
    saved = list(tmp_path.glob("*.md"))
    assert len(saved) == 1
    assert saved[0].read_text() == "# Report"
