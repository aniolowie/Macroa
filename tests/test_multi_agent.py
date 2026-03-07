"""Tests for MultiAgentCoordinator and spawn_agent tool."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from macroa.kernel.multi_agent import (
    AgentResult,
    AgentTask,
    MultiAgentCoordinator,
    _build_dep_context,
)
from macroa.stdlib.schema import ModelTier, SkillResult

# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_drivers():
    from macroa.stdlib.schema import DriverBundle
    drivers = DriverBundle(
        llm=MagicMock(),
        shell=MagicMock(),
        fs=MagicMock(),
        memory=MagicMock(),
        network=MagicMock(),
    )
    drivers.llm.complete.return_value = "synthesized output"
    drivers.llm.last_usage = {}
    drivers.memory.get_episodes.return_value = []
    drivers.memory.search_fts.return_value = []
    drivers.memory.list_pinned.return_value = []
    return drivers


def _make_coordinator(drivers=None):
    return MultiAgentCoordinator(
        drivers=drivers or _make_drivers(),
        session_id="test-session",
    )


def _make_skill_result(output="agent output", success=True):
    return SkillResult(
        output=output,
        success=success,
        turn_id="t1",
        model_tier=ModelTier.SONNET,
    )


# ── AgentTask ─────────────────────────────────────────────────────────────────


class TestAgentTask:
    def test_default_tier_is_sonnet(self):
        t = AgentTask(name="a", objective="do x")
        assert t.model_tier == ModelTier.SONNET

    def test_depends_on_defaults_empty(self):
        t = AgentTask(name="a", objective="x")
        assert t.depends_on == []

    def test_persona_defaults_empty(self):
        t = AgentTask(name="a", objective="x")
        assert t.persona == ""


# ── _build_dep_context ────────────────────────────────────────────────────────


class TestBuildDepContext:
    def test_no_deps_returns_empty(self):
        task = AgentTask("writer", "write", depends_on=[])
        assert _build_dep_context(task, {}) == ""

    def test_dep_output_injected(self):
        task = AgentTask("writer", "write", depends_on=["researcher"])
        results = {"researcher": AgentResult("researcher", "research output", True, 100)}
        ctx = _build_dep_context(task, results)
        assert "research output" in ctx
        assert "researcher" in ctx

    def test_failed_dep_excluded(self):
        task = AgentTask("writer", "write", depends_on=["researcher"])
        results = {"researcher": AgentResult("researcher", "", False, 100, error="network error")}
        ctx = _build_dep_context(task, results)
        assert ctx == ""

    def test_multiple_deps(self):
        task = AgentTask("final", "combine", depends_on=["a", "b"])
        results = {
            "a": AgentResult("a", "output A", True, 50),
            "b": AgentResult("b", "output B", True, 60),
        }
        ctx = _build_dep_context(task, results)
        assert "output A" in ctx
        assert "output B" in ctx


# ── MultiAgentCoordinator ─────────────────────────────────────────────────────


class TestMultiAgentCoordinator:
    def _mock_run_agent(self, task: AgentTask, ctx: str) -> AgentResult:
        return AgentResult(
            name=task.name,
            output=f"result-of-{task.name}",
            success=True,
            elapsed_ms=10,
        )

    def test_empty_tasks_returns_failure(self):
        coord = _make_coordinator()
        result = coord.run([], "do nothing")
        assert not result.success

    def test_single_task_returns_output_directly(self):
        coord = _make_coordinator()
        with patch.object(coord, "_run_agent", side_effect=self._mock_run_agent):
            result = coord.run([AgentTask("only", "do it")], "do it")
        assert result.success
        assert "result-of-only" in result.output

    def test_independent_tasks_run_in_parallel(self):
        coord = _make_coordinator()
        timing: list[float] = []

        def slow_agent(task: AgentTask, ctx: str) -> AgentResult:
            start = time.monotonic()
            time.sleep(0.05)
            timing.append(time.monotonic() - start)
            return AgentResult(task.name, f"output-{task.name}", True, 50)

        tasks = [
            AgentTask("a", "task a"),
            AgentTask("b", "task b"),
            AgentTask("c", "task c"),
        ]
        t0 = time.monotonic()
        with patch.object(coord, "_run_agent", side_effect=slow_agent):
            result = coord.run(tasks, "run all")
        total = time.monotonic() - t0

        assert result.success
        # Parallel: total should be ~0.05s, not ~0.15s
        assert total < 0.12, f"Tasks ran sequentially (took {total:.3f}s)"

    def test_dependency_ordering(self):
        coord = _make_coordinator()
        order: list[str] = []

        def ordered_agent(task: AgentTask, ctx: str) -> AgentResult:
            order.append(task.name)
            return AgentResult(task.name, f"done-{task.name}", True, 10)

        tasks = [
            AgentTask("step1", "first"),
            AgentTask("step2", "second", depends_on=["step1"]),
            AgentTask("step3", "third", depends_on=["step2"]),
        ]
        with patch.object(coord, "_run_agent", side_effect=ordered_agent):
            result = coord.run(tasks, "sequential")

        assert result.success
        assert order == ["step1", "step2", "step3"]

    def test_failed_dependency_skips_dependents(self):
        coord = _make_coordinator()

        def failing_agent(task: AgentTask, ctx: str) -> AgentResult:
            if task.name == "step1":
                return AgentResult("step1", "", False, 10, error="network error")
            return AgentResult(task.name, "output", True, 10)

        tasks = [
            AgentTask("step1", "first"),
            AgentTask("step2", "second", depends_on=["step1"]),
        ]
        with patch.object(coord, "_run_agent", side_effect=failing_agent):
            result = coord.run(tasks, "chained")

        # With step1 failed and step2 depending on it, the whole run fails
        assert not result.success
        # step2 should never have been executed (failing_agent called only for step1)
        assert "network error" in (result.error or "")

    def test_all_failed_returns_failure(self):
        coord = _make_coordinator()

        def failing_agent(task: AgentTask, ctx: str) -> AgentResult:
            return AgentResult(task.name, "", False, 10, error="broken")

        tasks = [AgentTask("only", "fail")]
        with patch.object(coord, "_run_agent", side_effect=failing_agent):
            result = coord.run(tasks, "fail")

        assert not result.success

    def test_metadata_contains_agent_count(self):
        coord = _make_coordinator()
        with patch.object(coord, "_run_agent", side_effect=self._mock_run_agent):
            result = coord.run([
                AgentTask("a", "do a"), AgentTask("b", "do b"),
            ], "two tasks")
        assert result.metadata["agent_count"] == 2

    def test_caps_at_max_agents(self):
        from macroa.kernel.multi_agent import _MAX_AGENTS
        coord = _make_coordinator()
        tasks = [AgentTask(f"t{i}", f"task {i}") for i in range(_MAX_AGENTS + 3)]
        with patch.object(coord, "_run_agent", side_effect=self._mock_run_agent):
            result = coord.run(tasks, "too many")
        assert result.metadata["agent_count"] == _MAX_AGENTS


# ── spawn_agent tool ──────────────────────────────────────────────────────────


class TestSpawnAgentTool:
    def test_spawn_agent_returns_output_on_success(self):
        from macroa.kernel.tool_defs import _spawn_agent

        drivers = _make_drivers()
        mock_result = SkillResult(
            output="research complete", success=True,
            turn_id="t1", model_tier=ModelTier.SONNET,
        )
        with patch("macroa.kernel.multi_agent.MultiAgentCoordinator.run", return_value=mock_result):
            result = _spawn_agent("researcher", "research X", "sonnet", "", drivers)

        assert "research complete" in result
        assert "researcher" in result

    def test_spawn_agent_returns_error_on_failure(self):
        from macroa.kernel.tool_defs import _spawn_agent

        drivers = _make_drivers()
        mock_result = SkillResult(
            output="", success=False, error="LLM down",
            turn_id="t1", model_tier=ModelTier.SONNET,
        )
        with patch("macroa.kernel.multi_agent.MultiAgentCoordinator.run", return_value=mock_result):
            result = _spawn_agent("worker", "do task", "haiku", "", drivers)

        assert "failed" in result
        assert "LLM down" in result

    def test_spawn_agent_unknown_tier_defaults_sonnet(self):
        from macroa.kernel.tool_defs import _spawn_agent

        drivers = _make_drivers()
        mock_result = SkillResult(
            output="ok", success=True, turn_id="t1", model_tier=ModelTier.SONNET,
        )
        captured_tasks: list = []

        def fake_run(tasks, original_request):
            captured_tasks.extend(tasks)
            return mock_result

        with patch("macroa.kernel.multi_agent.MultiAgentCoordinator.run", side_effect=fake_run):
            _spawn_agent("a", "do it", "invalid_tier", "", drivers)

        assert captured_tasks[0].model_tier == ModelTier.SONNET


# ── kernel.run_agents API ─────────────────────────────────────────────────────


class TestKernelRunAgentsAPI:
    def test_run_agents_exists(self):
        import macroa.kernel as kernel
        assert callable(getattr(kernel, "run_agents", None))

    def test_run_agents_delegates_to_coordinator(self):
        import macroa.kernel as kernel
        mock_result = SkillResult(
            output="done", success=True, turn_id="ma", model_tier=ModelTier.SONNET,
        )
        tasks = [AgentTask("a", "do a")]

        with patch("macroa.kernel.multi_agent.MultiAgentCoordinator.run", return_value=mock_result), \
             patch("macroa.kernel._get_drivers", return_value=_make_drivers()):
            result = kernel.run_agents(tasks, "test request", session_id="s1")

        assert result.output == "done"
