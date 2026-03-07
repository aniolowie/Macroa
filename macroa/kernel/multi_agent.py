"""Multi-agent coordinator — run independent AgentLoop instances in parallel.

Architecture
------------
                         ┌─────────────┐
                         │  Coordinator │
                         └──────┬───────┘
                                │ decomposes task
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
             Agent-0       Agent-1       Agent-2
           (research)     (analysis)    (writing)
              thread        thread       thread
                    │           │           │
                    └───────────┴───────────┘
                                │ merge
                         ┌──────▼───────┐
                         │  Synthesizer  │
                         └──────────────┘

Key differences from Planner (sequential):
  - Each AgentTask runs in its own AgentLoop (full tool access per agent)
  - Independent tasks execute in parallel (threads)
  - Dependency edges: task B can depend on task A's output
  - Shared IPC bus for inter-agent messaging during execution
  - Results merged by a HAIKU synthesizer
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from macroa.stdlib.schema import Context, DriverBundle, Intent, ModelTier, SkillResult

logger = logging.getLogger(__name__)

_MAX_AGENTS = 8         # safety cap
_AGENT_TIMEOUT = 120    # seconds before an individual agent is force-abandoned


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class AgentTask:
    """A single named unit of work for a subagent."""
    name: str                           # short ID, e.g. "research", "writer"
    objective: str                      # full natural-language instruction
    model_tier: ModelTier = ModelTier.SONNET
    depends_on: list[str] = field(default_factory=list)  # names of prerequisite tasks
    persona: str = ""                   # optional system-level role hint


@dataclass
class AgentResult:
    name: str
    output: str
    success: bool
    elapsed_ms: int
    error: str | None = None


# ── Coordinator ───────────────────────────────────────────────────────────────

class MultiAgentCoordinator:
    """Runs a set of AgentTasks respecting dependency order and parallelism.

    Usage::

        coordinator = MultiAgentCoordinator(drivers, session_id)
        tasks = [
            AgentTask("research", "Research the history of RNA vaccines", ModelTier.SONNET),
            AgentTask("writer", "Write a 3-paragraph summary using the research",
                      ModelTier.SONNET, depends_on=["research"]),
        ]
        results = coordinator.run(tasks, original_request="Explain mRNA vaccines")
        final_output = results.output
    """

    def __init__(self, drivers: DriverBundle, session_id: str) -> None:
        self._drivers = drivers
        self._session_id = session_id

    def run(self, tasks: list[AgentTask], original_request: str) -> SkillResult:
        """Execute tasks respecting dependencies; merge outputs into one response."""
        if not tasks:
            return SkillResult(
                output="No tasks provided.",
                success=False,
                turn_id="multi-agent",
                model_tier=ModelTier.HAIKU,
            )

        tasks = tasks[:_MAX_AGENTS]
        t0 = time.monotonic()

        # Execute with dependency ordering
        results: dict[str, AgentResult] = {}
        self._execute_dag(tasks, results)

        # Synthesize final output
        successful = [r for r in results.values() if r.success]
        if not successful:
            first_error = next((r.error for r in results.values() if r.error), "all agents failed")
            return SkillResult(
                output="",
                success=False,
                error=f"Multi-agent run failed: {first_error}",
                turn_id="multi-agent",
                model_tier=ModelTier.HAIKU,
            )

        final = self._synthesize(original_request, results, tasks)
        elapsed = int((time.monotonic() - t0) * 1000)

        return SkillResult(
            output=final,
            success=True,
            turn_id="multi-agent",
            model_tier=ModelTier.SONNET,
            metadata={
                "skill": "multi_agent",
                "agent_count": len(tasks),
                "elapsed_ms": elapsed,
                "agents": {name: r.success for name, r in results.items()},
            },
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _execute_dag(self, tasks: list[AgentTask], results: dict[str, AgentResult]) -> None:
        """Topological wave execution: run all tasks whose dependencies are met."""
        pending = {t.name: t for t in tasks}
        completed: set[str] = set()
        failed: set[str] = set()

        while pending:
            # Find tasks whose dependencies are all complete
            ready = [
                t for t in pending.values()
                if all(dep in completed for dep in t.depends_on)
                and not any(dep in failed for dep in t.depends_on)
            ]

            if not ready:
                # Dependency cycle or all remaining depend on failed tasks
                for t in pending.values():
                    results[t.name] = AgentResult(
                        name=t.name,
                        output="",
                        success=False,
                        elapsed_ms=0,
                        error="dependency failed or cycle detected",
                    )
                break

            # Run ready tasks in parallel
            thread_results: dict[str, AgentResult] = {}
            lock = threading.Lock()

            def _run_one(task: AgentTask, context_injection: str) -> None:
                res = self._run_agent(task, context_injection)
                with lock:
                    thread_results[task.name] = res

            # Build context from completed dependency outputs
            threads: list[threading.Thread] = []
            for task in ready:
                dep_context = _build_dep_context(task, results)
                t = threading.Thread(
                    target=_run_one,
                    args=(task, dep_context),
                    daemon=True,
                    name=f"macroa-agent-{task.name}",
                )
                threads.append(t)
                t.start()

            for t in threads:
                t.join(timeout=_AGENT_TIMEOUT)

            # Collect results; mark timed-out agents as failed
            for task in ready:
                if task.name in thread_results:
                    results[task.name] = thread_results[task.name]
                else:
                    results[task.name] = AgentResult(
                        name=task.name,
                        output="",
                        success=False,
                        elapsed_ms=_AGENT_TIMEOUT * 1000,
                        error=f"agent {task.name!r} timed out after {_AGENT_TIMEOUT}s",
                    )
                    logger.warning("Agent %r timed out", task.name)

                if results[task.name].success:
                    completed.add(task.name)
                else:
                    failed.add(task.name)
                del pending[task.name]

    def _run_agent(self, task: AgentTask, context_injection: str) -> AgentResult:
        """Run a single AgentLoop for one task."""
        from macroa.kernel.agent import AgentLoop
        from macroa.kernel import _get_or_create_session as get_or_create_context

        t0 = time.monotonic()

        # Each sub-agent gets an ephemeral session derived from parent
        sub_session = f"{self._session_id}__agent_{task.name}"

        # Build intent
        objective = task.objective
        if context_injection:
            objective = f"{context_injection}\n\n---\n\nYour task: {task.objective}"

        intent = Intent(
            raw=objective,
            skill_name="agent_skill",
            parameters={},
            model_tier=task.model_tier,
            routing_confidence=1.0,
            turn_id=f"multi-{task.name}",
        )

        context_mgr = get_or_create_context(sub_session)

        # Inject persona into context if provided
        if task.persona:
            context_mgr.add_system(
                turn_id=f"persona-{task.name}",
                content=f"You are acting as: {task.persona}",
                pinned=True,
            )

        context = context_mgr.snapshot()

        try:
            loop = AgentLoop(
                drivers=self._drivers,
                confirm_callback=None,   # subagents auto-approve (coordinator context)
                session_approved=set(),
            )
            result = loop.run(intent, context)
            elapsed = int((time.monotonic() - t0) * 1000)
            return AgentResult(
                name=task.name,
                output=result.output or "",
                success=result.success,
                elapsed_ms=elapsed,
                error=result.error,
            )
        except Exception as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.error("Agent %r raised: %s", task.name, exc, exc_info=True)
            return AgentResult(
                name=task.name,
                output="",
                success=False,
                elapsed_ms=elapsed,
                error=str(exc),
            )

    def _synthesize(
        self,
        original_request: str,
        results: dict[str, AgentResult],
        tasks: list[AgentTask],
    ) -> str:
        """Combine all successful agent outputs into one coherent response."""
        successful = {name: r for name, r in results.items() if r.success}
        if not successful:
            return "\n\n".join(
                f"**{name}** (failed): {r.error}" for name, r in results.items()
            )

        if len(successful) == 1:
            return next(iter(successful.values())).output

        # Build synthesis prompt ordered by task dependency (final tasks last)
        task_order = [t.name for t in tasks]
        sections = []
        for name in task_order:
            if name in successful:
                sections.append(f"[{name}]\n{successful[name].output}")

        combined = "\n\n".join(sections)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are assembling outputs from multiple specialised AI agents into "
                    "one coherent, well-structured response. Write as if you produced the "
                    "whole thing in one pass — do not mention agents or subtasks. "
                    "Preserve all key information. Be concise and direct."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original request: {original_request}\n\n"
                    f"Agent outputs:\n{combined}"
                ),
            },
        ]
        try:
            return self._drivers.llm.complete(messages=messages, tier=ModelTier.HAIKU)
        except Exception as exc:
            logger.warning("Multi-agent synthesizer failed (%s) — concatenating", exc)
            return "\n\n---\n\n".join(r.output for r in successful.values())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_dep_context(task: AgentTask, results: dict[str, AgentResult]) -> str:
    """Build context string from completed dependency outputs."""
    if not task.depends_on:
        return ""
    parts = []
    for dep in task.depends_on:
        if dep in results and results[dep].success:
            parts.append(f"Output from '{dep}':\n{results[dep].output}")
    return "\n\n".join(parts)


# ── Intent import helper ──────────────────────────────────────────────────────

# Re-export for convenience
from macroa.stdlib.schema import Intent  # noqa: E402
