"""Tests for kernel components — no real LLM calls (mocked)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from macroa.kernel.context import ContextManager
from macroa.kernel.escalation import next_tier, resolve_tier
from macroa.stdlib.schema import (
    Context, DriverBundle, Intent, ModelTier, SkillManifest, SkillResult,
)


# ------------------------------------------------------------------ Escalation

def test_resolve_tier_keyword_opus():
    tier = resolve_tier("use the best model", None)
    assert tier == ModelTier.OPUS


def test_resolve_tier_keyword_sonnet():
    tier = resolve_tier("think carefully about this", None)
    assert tier == ModelTier.SONNET


def test_resolve_tier_keyword_haiku():
    tier = resolve_tier("quick answer please, use haiku", None)
    assert tier == ModelTier.HAIKU


def test_resolve_tier_skill_pinned():
    tier = resolve_tier("hello world", ModelTier.SONNET)
    assert tier == ModelTier.SONNET


def test_resolve_tier_default():
    # Default is now NANO — the microcontroller tier
    tier = resolve_tier("hello world", None)
    assert tier == ModelTier.NANO


def test_next_tier_nano_to_haiku():
    assert next_tier(ModelTier.NANO) == ModelTier.HAIKU


def test_next_tier_haiku_to_sonnet():
    assert next_tier(ModelTier.HAIKU) == ModelTier.SONNET


def test_next_tier_sonnet_to_opus():
    assert next_tier(ModelTier.SONNET) == ModelTier.OPUS


def test_next_tier_opus_stays():
    assert next_tier(ModelTier.OPUS) == ModelTier.OPUS


def test_full_escalation_chain():
    # NANO → HAIKU → SONNET → OPUS → OPUS (ceiling)
    chain = [ModelTier.NANO]
    while chain[-1] != ModelTier.OPUS:
        chain.append(next_tier(chain[-1]))
    assert chain == [ModelTier.NANO, ModelTier.HAIKU, ModelTier.SONNET, ModelTier.OPUS]


# ------------------------------------------------------------------ ContextManager

def test_context_manager_add_and_snapshot():
    mgr = ContextManager(window_size=5)
    mgr.add_user("turn-1", "hello")
    snap = mgr.snapshot()
    assert len(snap.entries) == 1
    assert snap.entries[0].content == "hello"
    assert snap.entries[0].role == "user"


def test_context_manager_assistant_entry():
    mgr = ContextManager(window_size=5)
    result = SkillResult(output="world", success=True, turn_id="t1")
    mgr.add_assistant(result)
    snap = mgr.snapshot()
    assert snap.entries[0].role == "assistant"
    assert snap.entries[0].content == "world"


def test_context_manager_clear():
    mgr = ContextManager(window_size=5)
    mgr.add_user("t1", "hello")
    mgr.clear()
    assert mgr.snapshot().entries == []


def test_context_manager_rolling_window():
    mgr = ContextManager(window_size=2)  # maxlen=4 (2 turns × 2)
    for i in range(10):
        mgr.add_user(f"t{i}", f"msg{i}")
    snap = mgr.snapshot()
    # Should only have the last 4 entries
    assert len(snap.entries) <= 4


def test_context_manager_pinned_not_evicted():
    mgr = ContextManager(window_size=2)  # maxlen=4
    mgr.add_system("sys-1", "Always remember this", pinned=True)
    for i in range(10):
        mgr.add_user(f"t{i}", f"msg{i}")
    snap = mgr.snapshot()
    pinned = [e for e in snap.entries if e.pinned]
    assert len(pinned) == 1
    assert pinned[0].content == "Always remember this"


# ------------------------------------------------------------------ Skills (unit, no LLM)

def _make_drivers(tmp_path: Path) -> DriverBundle:
    from macroa.drivers.fs_driver import FSDriver
    from macroa.drivers.memory_driver import MemoryDriver
    from macroa.drivers.shell_driver import ShellDriver
    from macroa.drivers.network_driver import NetworkDriver
    return DriverBundle(
        llm=MagicMock(),
        shell=ShellDriver(),
        fs=FSDriver(base_dir=tmp_path),
        memory=MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db"),
        network=NetworkDriver(),
    )


def _intent(skill: str, params: dict, raw: str = "test") -> Intent:
    return Intent(
        raw=raw,
        skill_name=skill,
        parameters=params,
        model_tier=ModelTier.HAIKU,
        routing_confidence=1.0,
        turn_id=str(uuid.uuid4()),
    )


def _ctx() -> Context:
    return Context(entries=[], session_id="test-session")


def test_shell_skill_echo(tmp_path):
    from macroa.skills.shell_skill import run
    intent = _intent("shell_skill", {"command": "echo hello"})
    result = run(intent, _ctx(), _make_drivers(tmp_path))
    assert result.success
    assert "hello" in result.output


def test_shell_skill_no_command(tmp_path):
    from macroa.skills.shell_skill import run
    intent = _intent("shell_skill", {})
    result = run(intent, _ctx(), _make_drivers(tmp_path))
    assert not result.success


def test_memory_skill_set_get(tmp_path):
    from macroa.skills.memory_skill import run
    drivers = _make_drivers(tmp_path)
    ctx = _ctx()

    set_intent = _intent("memory_skill", {"action": "set", "key": "server_ip", "value": "192.168.1.100"})
    result = run(set_intent, ctx, drivers)
    assert result.success
    assert "192.168.1.100" in result.output

    get_intent = _intent("memory_skill", {"action": "get", "key": "server_ip"})
    result2 = run(get_intent, ctx, drivers)
    assert result2.success
    assert "192.168.1.100" in result2.output


def test_memory_skill_search(tmp_path):
    from macroa.skills.memory_skill import run
    drivers = _make_drivers(tmp_path)
    ctx = _ctx()

    drivers.memory.set("user", "color", "blue")
    search_intent = _intent("memory_skill", {"action": "search", "query": "color"})
    result = run(search_intent, ctx, drivers)
    assert result.success
    assert "blue" in result.output


def test_file_skill_read_write(tmp_path):
    from macroa.skills.file_skill import run
    drivers = _make_drivers(tmp_path)
    ctx = _ctx()
    path = str(tmp_path / "hello.txt")

    write_intent = _intent("file_skill", {"action": "write", "path": path, "content": "test content"})
    w_result = run(write_intent, ctx, drivers)
    assert w_result.success

    read_intent = _intent("file_skill", {"action": "read", "path": path})
    r_result = run(read_intent, ctx, drivers)
    assert r_result.success
    assert "test content" in r_result.output
