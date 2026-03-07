"""Tests for the identity module and chat_skill memory injection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from macroa.kernel.identity import build_system_prompt
from macroa.stdlib.schema import DriverBundle, Intent, ModelTier

# ------------------------------------------------------------------ identity.build_system_prompt


def _identity_dir(tmp_path: Path) -> Path:
    """Create and return a tmp identity subdir, mirroring ~/.macroa/identity/."""
    d = tmp_path / "identity"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_bootstrap_no_identity(tmp_path: Path):
    idir = _identity_dir(tmp_path)
    with patch("macroa.kernel.identity._IDENTITY_DIR", idir):
        result = build_system_prompt()
    # No IDENTITY.md → should return bootstrap content
    assert "Who am I" in result or "woke up" in result


def test_bootstrap_writes_file(tmp_path: Path):
    idir = _identity_dir(tmp_path)
    with patch("macroa.kernel.identity._IDENTITY_DIR", idir):
        build_system_prompt()
    assert (idir / "BOOTSTRAP.md").exists()


def test_bootstrap_custom_file(tmp_path: Path):
    idir = _identity_dir(tmp_path)
    (idir / "BOOTSTRAP.md").write_text("Custom bootstrap content.", encoding="utf-8")
    with patch("macroa.kernel.identity._IDENTITY_DIR", idir):
        result = build_system_prompt()
    # Bootstrap content is always augmented with capabilities + safety sections
    assert result.startswith("Custom bootstrap content.")


def test_identity_files_loaded(tmp_path: Path):
    idir = _identity_dir(tmp_path)
    (idir / "IDENTITY.md").write_text("Name: Pixel\nEmoji: 🤖", encoding="utf-8")
    (idir / "USER.md").write_text("Name: Alice\nTimezone: UTC", encoding="utf-8")
    (idir / "SOUL.md").write_text("Be warm and curious.", encoding="utf-8")
    with patch("macroa.kernel.identity._IDENTITY_DIR", idir):
        result = build_system_prompt()
    assert "Pixel" in result
    assert "Alice" in result
    assert "warm and curious" in result


def test_identity_only_required_file(tmp_path: Path):
    idir = _identity_dir(tmp_path)
    (idir / "IDENTITY.md").write_text("Name: TestAgent", encoding="utf-8")
    with patch("macroa.kernel.identity._IDENTITY_DIR", idir):
        result = build_system_prompt()
    assert "TestAgent" in result


def test_empty_identity_file_has_structure(tmp_path: Path):
    idir = _identity_dir(tmp_path)
    (idir / "IDENTITY.md").write_text("", encoding="utf-8")
    with patch("macroa.kernel.identity._IDENTITY_DIR", idir):
        result = build_system_prompt()
    # Even with empty IDENTITY.md: runtime, capabilities, and safety sections always present
    assert "Runtime" in result
    assert "Your Capabilities" in result
    assert "Safety" in result


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
    memory.get_episodes.return_value = []   # no compacted episodes by default
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
    facts = [{"key": "name", "value": "Alice", "pinned": True, "confidence": 1.0}]

    with patch("macroa.skills.chat_skill.build_system_prompt", return_value="Base prompt."), \
         patch("macroa.skills.chat_skill.retrieve", return_value=facts):
        result = _build_system(intent, drivers)

    assert "name" in result
    assert "Alice" in result
    assert "Base prompt." in result


def test_chat_skill_no_memory_no_injection(tmp_path: Path):
    from macroa.skills.chat_skill import _build_system

    drivers = _make_drivers([])
    intent = _make_intent("hello")

    with patch("macroa.skills.chat_skill.build_system_prompt", return_value="Base prompt."), \
         patch("macroa.skills.chat_skill.retrieve", return_value=[]):
        result = _build_system(intent, drivers)

    assert result == "Base prompt."


def test_chat_skill_memory_error_ignored():
    from macroa.skills.chat_skill import _build_system

    memory = MagicMock()
    memory.search.side_effect = RuntimeError("DB gone")
    memory.get_episodes.return_value = []
    drivers = DriverBundle(
        llm=MagicMock(), shell=MagicMock(), fs=MagicMock(), memory=memory, network=MagicMock()
    )
    intent = _make_intent("hello")

    with patch("macroa.skills.chat_skill.build_system_prompt", return_value="Base."):
        result = _build_system(intent, drivers)

    # Exception swallowed — base prompt returned unchanged
    assert result == "Base."
