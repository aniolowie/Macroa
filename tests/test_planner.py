"""Tests for the Planner — mocked LLM, no real API calls."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest

from macroa.kernel.planner import Plan, PlanStep, Planner, _is_trivially_atomic
from macroa.stdlib.schema import Context, ModelTier


def _mock_llm(response: str) -> MagicMock:
    llm = MagicMock()
    llm.complete.return_value = response
    return llm


def _ctx() -> Context:
    return Context(entries=[], session_id="test")


def _registry() -> MagicMock:
    r = MagicMock()
    r.names.return_value = ["chat_skill", "shell_skill", "memory_skill", "file_skill"]
    return r


# ------------------------------------------------------------------ heuristic

def test_trivially_atomic_shell():
    assert _is_trivially_atomic("!ls -la") is True
    assert _is_trivially_atomic("$pwd") is True


def test_trivially_atomic_short():
    assert _is_trivially_atomic("what is 2+2") is True
    assert _is_trivially_atomic("hi") is True


def test_trivially_atomic_memory_prefix():
    assert _is_trivially_atomic("remember my server IP is 10.0.0.1") is True


def test_not_trivially_atomic_long():
    long_input = "Please do my homework on the history of quantum computing. Research key facts, write an essay with introduction, body, and conclusion, then format it nicely."
    assert _is_trivially_atomic(long_input) is False


# ------------------------------------------------------------------ plan()

def test_plan_atomic_response():
    llm = _mock_llm(json.dumps({"is_complex": False, "steps": []}))
    planner = Planner(llm)
    # Long enough to bypass heuristic
    long_input = "x" * 90
    result = planner.plan(long_input, _ctx(), _registry())
    assert result is None


def test_plan_complex_response():
    steps_json = [
        {"subtask": "Research quantum computing", "tier": "nano"},
        {"subtask": "Write an essay", "tier": "sonnet"},
        {"subtask": "Format and conclude", "tier": "haiku"},
    ]
    llm = _mock_llm(json.dumps({"is_complex": True, "steps": steps_json}))
    planner = Planner(llm)
    # Input must exceed the 80-char heuristic threshold to reach the LLM
    long_input = "Do my complete homework on the history of quantum computing: introduction, body paragraphs, and a conclusion."
    result = planner.plan(long_input, _ctx(), _registry())
    assert result is not None
    assert isinstance(result, Plan)
    assert len(result.steps) == 3
    assert result.steps[0].model_tier == ModelTier.NANO
    assert result.steps[1].model_tier == ModelTier.SONNET
    assert result.steps[2].model_tier == ModelTier.HAIKU
    assert result.combine_tier == ModelTier.HAIKU


def test_plan_single_step_returns_none():
    """A plan with only 1 step isn't worth the overhead — treated as atomic."""
    llm = _mock_llm(json.dumps({"is_complex": True, "steps": [
        {"subtask": "Do the thing", "tier": "haiku"},
    ]}))
    planner = Planner(llm)
    result = planner.plan("x" * 90, _ctx(), _registry())
    assert result is None


def test_plan_invalid_tier_defaults_to_haiku():
    steps_json = [
        {"subtask": "Step one", "tier": "unknown_tier"},
        {"subtask": "Step two", "tier": "sonnet"},
    ]
    llm = _mock_llm(json.dumps({"is_complex": True, "steps": steps_json}))
    planner = Planner(llm)
    result = planner.plan("x" * 90, _ctx(), _registry())
    assert result is not None
    assert result.steps[0].model_tier == ModelTier.HAIKU  # defaulted


def test_plan_llm_failure_returns_none():
    from macroa.drivers.llm_driver import LLMDriverError
    llm = MagicMock()
    llm.complete.side_effect = LLMDriverError("api down")
    planner = Planner(llm)
    result = planner.plan("x" * 90, _ctx(), _registry())
    assert result is None


def test_plan_bad_json_returns_none():
    llm = _mock_llm("not valid json at all")
    planner = Planner(llm)
    result = planner.plan("x" * 90, _ctx(), _registry())
    assert result is None


# ------------------------------------------------------------------ combine()

def test_combine_returns_llm_output():
    llm = _mock_llm("Combined result here.")
    planner = Planner(llm)
    result = planner.combine(
        "do my homework",
        [("Research", "Quantum computing facts"), ("Essay", "A well-written essay")],
        ModelTier.HAIKU,
    )
    assert result == "Combined result here."


def test_combine_fallback_on_llm_failure():
    from macroa.drivers.llm_driver import LLMDriverError
    llm = MagicMock()
    llm.complete.side_effect = LLMDriverError("down")
    planner = Planner(llm)
    result = planner.combine(
        "original",
        [("Step A", "output A"), ("Step B", "output B")],
        ModelTier.HAIKU,
    )
    # Graceful fallback joins results with separator
    assert "output A" in result
    assert "output B" in result


# ------------------------------------------------------------------ heuristic bypasses LLM

def test_trivially_atomic_never_calls_llm():
    llm = MagicMock()
    planner = Planner(llm)
    result = planner.plan("!ls", _ctx(), _registry())
    assert result is None
    llm.complete.assert_not_called()  # LLM never touched
