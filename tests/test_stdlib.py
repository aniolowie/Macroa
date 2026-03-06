"""Tests for deterministic stdlib utilities."""

import pytest
from macroa.stdlib.text import (
    detect_escalation_tier,
    is_shell_prefix,
    normalize_whitespace,
    strip_ansi,
    strip_shell_prefix,
    truncate,
)
from macroa.stdlib.schema import ModelTier, Intent, SkillResult


def test_strip_ansi():
    assert strip_ansi("\x1b[32mHello\x1b[0m") == "Hello"
    assert strip_ansi("plain text") == "plain text"


def test_truncate():
    assert truncate("hello", 10) == "hello"
    result = truncate("hello world", 5)
    assert result.startswith("hello")
    assert "truncated" in result


def test_detect_escalation_tier_opus():
    assert detect_escalation_tier("use the best model for this") == "opus"
    assert detect_escalation_tier("opus please") == "opus"


def test_detect_escalation_tier_sonnet():
    assert detect_escalation_tier("think carefully about this") == "sonnet"
    assert detect_escalation_tier("use best reasoning") == "sonnet"


def test_detect_escalation_tier_none():
    assert detect_escalation_tier("what is the capital of France?") is None


def test_is_shell_prefix():
    assert is_shell_prefix("!ls -la")
    assert is_shell_prefix("$pwd")
    assert not is_shell_prefix("run ls command")


def test_strip_shell_prefix():
    assert strip_shell_prefix("!ls -la") == "ls -la"
    assert strip_shell_prefix("$ pwd") == "pwd"
    assert strip_shell_prefix("hello") == "hello"


def test_normalize_whitespace():
    assert normalize_whitespace("hello   world\n\t!") == "hello world !"


def test_model_tier_values():
    assert ModelTier.HAIKU == "haiku"
    assert ModelTier.SONNET == "sonnet"
    assert ModelTier.OPUS == "opus"


def test_skill_result_defaults():
    r = SkillResult(output="ok", success=True)
    assert r.needs_reasoning is False
    assert r.error is None
    assert r.pin_to_context is False
    assert r.model_tier == ModelTier.NANO  # default is the microcontroller tier
