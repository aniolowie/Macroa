"""Tests for the identity module and chat_skill memory injection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from macroa.kernel.identity import _FALLBACK, build_system_prompt
from macroa.stdlib.schema import DriverBundle, Intent, ModelTier

# ------------------------------------------------------------------ identity.build_system_prompt


def test_bootstrap_no_identity(tmp_path: Path):
    with patch("macroa.kernel.identity._MACROA_DIR", tmp_path):
        result = build_system_prompt()
    # No IDENTITY.md → should return bootstrap content
    assert "Who am I" in result or "woke up" in result


def test_bootstrap_writes_file(tmp_path: Path):
    with patch("macroa.kernel.identity._MACROA_DIR", tmp_path):
        build_system_prompt()
    assert (tmp_path / "BOOTSTRAP.md").exists()


def test_bootstrap_custom_file(tmp_path: Path):
    (tmp_path / "BOOTSTRAP.md").write_text("Custom bootstrap content.", encoding="utf-8")
    with patch("macroa.kernel.identity._MACROA_DIR", tmp_path):
        result = build_system_prompt()
    assert result == "Custom bootstrap content."


def test_identity_files_loaded(tmp_path: Path):
    (tmp_path / "IDENTITY.md").write_text("Name: Pixel\nEmoji: 🤖", encoding="utf-8")
    (tmp_path / "USER.md").write_text("Name: Alice\nTimezone: UTC", encoding="utf-8")
    (tmp_path / "SOUL.md").write_text("Be warm and curious.", encoding="utf-8")
    with patch("macroa.kernel.identity._MACROA_DIR", tmp_path):
        result = build_system_prompt()
    assert "Pixel" in result
    assert "Alice" in result
    assert "warm and curious" in result


def test_identity_only_required_file(tmp_path: Path):
    (tmp_path / "IDENTITY.md").write_text("Name: Macroa", encoding="utf-8")
    with patch("macroa.kernel.identity._MACROA_DIR", tmp_path):
        result = build_system_prompt()
    assert "Macroa" in result


def test_empty_identity_file_returns_fallback(tmp_path: Path):
    (tmp_path / "IDENTITY.md").write_text("", encoding="utf-8")
    with patch("macroa.kernel.identity._MACROA_DIR", tmp_path):
        result = build_system_prompt()
    # Fallback text is present; capabilities section is always appended
    assert _FALLBACK in result
    assert "Macroa Capabilities" in result


# ------------------------------------------------------------------ chat_skill memory injection


def _make_intent(raw: str) -> Intent:
    return Intent(
        raw=raw,
        skill_name="chat_skill",
        parameters={},
        model_tier=ModelTier.NANO,
        routing_confidence=0.0,
        turn_id="test-turn",
    )


def _make_drivers(memory_results: list[dict]) -> DriverBundle:
    memory = MagicMock()
    memory.search.return_value = memory_results
    llm = MagicMock()
    llm.complete.return_value = "Hello!"
    return DriverBundle(
        llm=llm,
        shell=MagicMock(),
        fs=MagicMock(),
        memory=memory,
        network=MagicMock(),
    )


def test_chat_skill_injects_memory(tmp_path: Path):
    from macroa.skills.chat_skill import _build_system

    drivers = _make_drivers([{"key": "name", "value": "Alice"}])
    intent = _make_intent("what is my name")

    with patch("macroa.skills.chat_skill.build_system_prompt", return_value="Base prompt."):
        result = _build_system(intent, drivers)

    assert "name: Alice" in result
    assert "Base prompt." in result


def test_chat_skill_no_memory_no_injection(tmp_path: Path):
    from macroa.skills.chat_skill import _build_system

    drivers = _make_drivers([])
    intent = _make_intent("hello")

    with patch("macroa.skills.chat_skill.build_system_prompt", return_value="Base prompt."):
        result = _build_system(intent, drivers)

    assert result == "Base prompt."


def test_chat_skill_memory_error_ignored():
    from macroa.skills.chat_skill import _build_system

    memory = MagicMock()
    memory.search.side_effect = RuntimeError("DB gone")
    drivers = DriverBundle(
        llm=MagicMock(), shell=MagicMock(), fs=MagicMock(), memory=memory, network=MagicMock()
    )
    intent = _make_intent("hello")

    with patch("macroa.skills.chat_skill.build_system_prompt", return_value="Base."):
        result = _build_system(intent, drivers)

    # Exception swallowed — base prompt returned unchanged
    assert result == "Base."
