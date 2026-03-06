"""Tests for the tools layer — no real APIs, no file-system side effects."""

from __future__ import annotations

import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from macroa.config.skill_registry import SkillEntry, SkillRegistry
from macroa.stdlib.schema import Context, DriverBundle, Intent, ModelTier, SkillResult
from macroa.tools.base import BaseTool, ToolManifest
from macroa.tools.heartbeat import HeartbeatManager
from macroa.tools.registry import ToolEntry, ToolRegistry
from macroa.tools.runner import ToolRunner


# ------------------------------------------------------------------ fixtures

def _intent(raw: str = "call me") -> Intent:
    return Intent(
        raw=raw,
        skill_name="call_me",
        parameters={},
        model_tier=ModelTier.NANO,
        routing_confidence=1.0,
        turn_id=str(uuid.uuid4()),
    )


def _ctx() -> Context:
    return Context(entries=[], session_id="test")


def _drivers() -> DriverBundle:
    return DriverBundle(
        llm=MagicMock(), shell=MagicMock(), fs=MagicMock(),
        memory=MagicMock(), network=MagicMock(),
    )


# ------------------------------------------------------------------ ToolManifest

def test_tool_manifest_defaults():
    m = ToolManifest(name="t", description="desc", triggers=["t"])
    assert m.version == "1.0.0"
    assert m.persistent is False
    assert m.timeout == 60
    assert m.model_tier is None


# ------------------------------------------------------------------ ToolRunner

class _OkTool(BaseTool):
    def execute(self, intent, context, drivers) -> SkillResult:
        return SkillResult(output="done", success=True, turn_id=intent.turn_id)


class _SlowTool(BaseTool):
    def execute(self, intent, context, drivers) -> SkillResult:
        time.sleep(10)
        return SkillResult(output="late", success=True)


class _ExplodingTool(BaseTool):
    def execute(self, intent, context, drivers) -> SkillResult:
        raise RuntimeError("boom")


def _manifest(name="t", timeout=5) -> ToolManifest:
    return ToolManifest(name=name, description="", triggers=[], timeout=timeout)


def test_runner_success():
    runner = ToolRunner(timeout=5)
    fn = runner.wrap(_OkTool(), _manifest())
    result = fn(_intent(), _ctx(), _drivers())
    assert result.success
    assert result.output == "done"


def test_runner_timeout():
    runner = ToolRunner(timeout=1)
    fn = runner.wrap(_SlowTool(), _manifest(timeout=1))
    result = fn(_intent(), _ctx(), _drivers())
    assert not result.success
    assert "timed out" in result.error


def test_runner_exception_isolation():
    runner = ToolRunner(timeout=5)
    fn = runner.wrap(_ExplodingTool(), _manifest())
    result = fn(_intent(), _ctx(), _drivers())
    assert not result.success
    assert "boom" in result.error


def test_runner_propagates_turn_id():
    runner = ToolRunner(timeout=5)

    class _NoTurnTool(BaseTool):
        def execute(self, intent, context, drivers) -> SkillResult:
            return SkillResult(output="x", success=True)  # no turn_id set

    fn = runner.wrap(_NoTurnTool(), _manifest())
    intent = _intent()
    result = fn(intent, _ctx(), _drivers())
    assert result.turn_id == intent.turn_id


# ------------------------------------------------------------------ ToolRegistry

def _write_tool(tool_dir: Path, code: str) -> None:
    tool_dir.mkdir(parents=True)
    (tool_dir / "tool.py").write_text(code)


def test_registry_loads_valid_tool(tmp_path):
    code = """
from macroa.tools.base import BaseTool, ToolManifest
from macroa.stdlib.schema import Context, DriverBundle, Intent, SkillResult

MANIFEST = ToolManifest(name="my_tool", description="test", triggers=["test"])

class MyTool(BaseTool):
    def execute(self, intent, context, drivers):
        return SkillResult(output="hi", success=True, turn_id=intent.turn_id)
"""
    _write_tool(tmp_path / "my_tool", code)
    reg = ToolRegistry()
    reg.load_from_dir(tmp_path)
    assert "my_tool" in reg._tools


def test_registry_skips_missing_manifest(tmp_path):
    code = """
from macroa.tools.base import BaseTool

class MyTool(BaseTool):
    def execute(self, intent, context, drivers): ...
"""
    _write_tool(tmp_path / "bad_tool", code)
    reg = ToolRegistry()
    reg.load_from_dir(tmp_path)
    assert len(reg._tools) == 0


def test_registry_skips_missing_class(tmp_path):
    code = """
from macroa.tools.base import ToolManifest

MANIFEST = ToolManifest(name="no_class", description="x", triggers=[])
# no BaseTool subclass
"""
    _write_tool(tmp_path / "no_class", code)
    reg = ToolRegistry()
    reg.load_from_dir(tmp_path)
    assert len(reg._tools) == 0


def test_registry_inject_into_skill_registry(tmp_path):
    code = """
from macroa.tools.base import BaseTool, ToolManifest
from macroa.stdlib.schema import Context, DriverBundle, Intent, SkillResult

MANIFEST = ToolManifest(name="injected", description="injected tool", triggers=["inject"])

class InjectedTool(BaseTool):
    def execute(self, intent, context, drivers):
        return SkillResult(output="injected", success=True, turn_id=intent.turn_id)
"""
    _write_tool(tmp_path / "injected", code)
    tool_reg = ToolRegistry()
    tool_reg.load_from_dir(tmp_path)

    skill_reg = SkillRegistry()
    tool_reg.inject_into(skill_reg)

    entry = skill_reg.get("injected")
    assert entry is not None
    assert "injected tool" in entry.manifest.description

    result = entry.run(_intent("inject"), _ctx(), _drivers())
    assert result.success
    assert result.output == "injected"


def test_registry_nonexistent_dir():
    reg = ToolRegistry()
    reg.load_from_dir(Path("/nonexistent/path/xyz"))  # should not raise
    assert len(reg._tools) == 0


# ------------------------------------------------------------------ HeartbeatManager

def test_heartbeat_calls_persistent_tools():
    tick_count = {"n": 0}

    class _PingTool(BaseTool):
        def execute(self, intent, context, drivers): ...
        def heartbeat(self, drivers):
            tick_count["n"] += 1

    manifest = ToolManifest(
        name="ping", description="", triggers=[], persistent=True
    )
    entry = ToolEntry(manifest=manifest, tool=_PingTool())
    tool_reg = MagicMock(spec=ToolRegistry)
    tool_reg.persistent_tools.return_value = [entry]

    hb = HeartbeatManager(tool_registry=tool_reg, drivers=_drivers(), interval=1)
    hb.start()
    time.sleep(1.5)
    hb.stop()

    assert tick_count["n"] >= 1


def test_heartbeat_no_persistent_tools():
    tool_reg = MagicMock(spec=ToolRegistry)
    tool_reg.persistent_tools.return_value = []
    hb = HeartbeatManager(tool_registry=tool_reg, drivers=_drivers(), interval=1)
    hb.start()
    assert not hb.running  # never started — no persistent tools


def test_heartbeat_isolates_tool_exceptions():
    """A heartbeat crash in one tool must not kill the heartbeat thread."""
    class _CrashTool(BaseTool):
        def execute(self, intent, context, drivers): ...
        def heartbeat(self, drivers):
            raise RuntimeError("heartbeat exploded")

    manifest = ToolManifest(name="crasher", description="", triggers=[], persistent=True)
    entry = ToolEntry(manifest=manifest, tool=_CrashTool())
    tool_reg = MagicMock(spec=ToolRegistry)
    tool_reg.persistent_tools.return_value = [entry]

    hb = HeartbeatManager(tool_registry=tool_reg, drivers=_drivers(), interval=1)
    hb.start()
    time.sleep(1.5)
    assert hb.running  # still alive despite crash
    hb.stop()
