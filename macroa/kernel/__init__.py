"""Public kernel API — kernel.run(input, session_id) is the only entry point."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from macroa.config.settings import get_settings
from macroa.config.skill_registry import SkillRegistry
from macroa.drivers.fs_driver import FSDriver
from macroa.drivers.llm_driver import LLMDriver
from macroa.drivers.memory_driver import MemoryDriver
from macroa.drivers.network_driver import NetworkDriver
from macroa.drivers.shell_driver import ShellDriver
from macroa.kernel.audit import AuditEntry, AuditLog
from macroa.kernel.budget import BudgetManager
from macroa.kernel.context import ContextManager
from macroa.kernel.dispatcher import Dispatcher
from macroa.kernel.events import Event, Events, bus
from macroa.kernel.ipc import IPCBus
from macroa.kernel.planner import Planner
from macroa.kernel.router import Router
from macroa.kernel.scheduler import Scheduler
from macroa.kernel.sessions import SessionStore
from macroa.kernel.watchdog import WatchdogManager
from macroa.stdlib.schema import DriverBundle, Intent, SkillResult
from macroa.tools.heartbeat import HeartbeatManager
from macroa.tools.registry import ToolRegistry
from macroa.vfs import VFS
from macroa.vfs.layout import MACROA_DIR, bootstrap_layout
from macroa.vfs.local import LocalBackend
from macroa.vfs.memory import MemoryBackend

logger = logging.getLogger(__name__)

ConfirmCallback = Callable[[str, str], bool]

# Blended cost per 1M tokens (input+output averaged) keyed on OpenRouter model ID.
# "Blended" means a single $/M figure that works reasonably for prompt+completion combined.
_COST_PER_MILLION: dict[str, float] = {
    "google/gemini-2.5-flash-lite":  0.18,
    "openai/gpt-5-nano":             0.14,
    "deepseek/deepseek-v3.2":        0.31,
    "openai/gpt-5-mini":             0.69,
    "google/gemini-2.5-flash":       0.85,
    "anthropic/claude-haiku-4-5":    2.00,
    "anthropic/claude-sonnet-4-6":   6.00,
    "anthropic/claude-opus-4-6":    10.00,
    "openai/gpt-5":                  3.44,
}


def _compute_cost(usage: dict) -> tuple[int, int, float]:
    """Return (prompt_tokens, completion_tokens, cost_usd) from llm.last_usage."""
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    model = usage.get("model", "")
    price = _COST_PER_MILLION.get(model, 0.0)
    cost = (prompt + completion) / 1_000_000 * price
    return prompt, completion, cost

# Thread-safe session store: session_id → ContextManager (in-process cache)
_sessions: dict[str, ContextManager] = {}
_sessions_lock = threading.Lock()

# Per-session sudo approved pattern keys (cleared when session is cleared)
_sudo_approved: dict[str, set[str]] = {}
_sudo_lock = threading.Lock()


def _get_sudo_approved(session_id: str) -> set[str]:
    with _sudo_lock:
        if session_id not in _sudo_approved:
            _sudo_approved[session_id] = set()
        return _sudo_approved[session_id]


def _is_first_boot() -> bool:
    return not (MACROA_DIR / "identity" / "IDENTITY.md").exists()

# Module-level singletons (lazy-initialized)
_drivers: DriverBundle | None = None
_registry: SkillRegistry | None = None
_tool_registry: ToolRegistry | None = None
_heartbeat: HeartbeatManager | None = None
_audit: AuditLog | None = None
_session_store: SessionStore | None = None
_scheduler: Scheduler | None = None
_watchdog: WatchdogManager | None = None
_extractor: object | None = None   # macroa.memory.extractor.MemoryExtractor
_compactor: object | None = None   # macroa.memory.compactor.ContextCompactor

# Skills that produce conversational output worth extracting user facts from
_EXTRACTABLE_SKILLS = frozenset({"chat_skill", "agent_skill", "research_skill"})


def _get_drivers() -> DriverBundle:
    global _drivers
    if _drivers is None:
        # Ensure ~/.macroa/ directory tree exists and migrate any legacy flat files
        bootstrap_layout()

        settings = get_settings()

        memory = MemoryDriver(
            backend=settings.memory_backend,
            db_path=settings.memory_db_path,
        )

        # Build the VFS — mount order doesn't matter; longest prefix always wins
        vfs = VFS()
        vfs.mount("/mem",       MemoryBackend(memory))
        vfs.mount("/identity",  LocalBackend(MACROA_DIR / "identity",         "identity"))
        vfs.mount("/workspace", LocalBackend(MACROA_DIR / "workspace",        "workspace"))
        vfs.mount("/research",  LocalBackend(MACROA_DIR / "research",         "research"))
        vfs.mount("/tools",     LocalBackend(MACROA_DIR / "tools",            "tools"))
        vfs.mount("/logs",      LocalBackend(MACROA_DIR / "logs",             "logs"))
        vfs.mount("/sessions",  LocalBackend(MACROA_DIR / "sessions",         "sessions"))
        vfs.mount("/fs",        LocalBackend(Path("/"),                        "fs"))

        budget = BudgetManager(
            budget_usd=settings.session_budget_usd,
            budget_tokens=settings.session_budget_tokens,
        )

        ipc = IPCBus()

        _drivers = DriverBundle(
            llm=LLMDriver(
                api_key=settings.openrouter_api_key,
                model_map=settings.model_map,
                http_referer=settings.http_referer,
                app_title=settings.app_title,
            ),
            shell=ShellDriver(),
            fs=FSDriver(),
            memory=memory,
            network=NetworkDriver(timeout=settings.network_timeout),
            vfs=vfs,
            budget=budget,
            ipc=ipc,
        )
    return _drivers


def _get_audit() -> AuditLog:
    global _audit
    if _audit is None:
        settings = get_settings()
        _audit = AuditLog(db_path=settings.audit_db_path)
    return _audit


def _get_registry() -> SkillRegistry:
    global _registry, _tool_registry, _heartbeat
    if _registry is None:
        settings = get_settings()
        drivers = _get_drivers()

        # Load built-in skills
        _registry = SkillRegistry()
        _registry.load_from_dir(settings.skills_dir)

        # Load tools (built-in examples + user-installed)
        # Builtin examples are reference implementations; skip setup() for them
        # (setup() is only meaningful for user-installed, fully-configured tools).
        _tool_registry = ToolRegistry()
        _tool_registry.load_from_dir(settings.builtin_tools_dir, None)
        _tool_registry.load_from_dir(settings.tools_dir, drivers)
        _tool_registry.inject_into(_registry)

        # Publish the live skill list to the identity layer so the agent
        # can accurately describe its own capabilities.
        from macroa.kernel.identity import set_runtime_skills
        set_runtime_skills(_registry.all_manifests())

        # Start heartbeat for any persistent tools
        _heartbeat = HeartbeatManager(
            tool_registry=_tool_registry,
            drivers=drivers,
            interval=settings.heartbeat_interval,
        )
        _heartbeat.start()

    return _registry


def _get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        settings = get_settings()
        _session_store = SessionStore(db_path=settings.sessions_db_path)
    return _session_store


def _get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        settings = get_settings()
        _scheduler = Scheduler(
            db_path=settings.scheduler_db_path,
            run_fn=run,
            poll_interval=settings.scheduler_poll,
        )
        _scheduler.start()
    return _scheduler


def _get_extractor():  # type: ignore[return]
    """Lazy-init the MemoryExtractor singleton."""
    global _extractor
    if _extractor is None:
        from macroa.memory.extractor import MemoryExtractor
        drivers = _get_drivers()
        _extractor = MemoryExtractor(llm=drivers.llm, memory=drivers.memory)
    return _extractor


def _get_compactor():  # type: ignore[return]
    """Lazy-init the ContextCompactor singleton."""
    global _compactor
    if _compactor is None:
        from macroa.memory.compactor import ContextCompactor
        drivers = _get_drivers()
        _compactor = ContextCompactor(llm=drivers.llm, memory=drivers.memory)
    return _compactor


def _get_watchdog() -> WatchdogManager:
    global _watchdog
    if _watchdog is None:
        settings = get_settings()
        drivers = _get_drivers()
        _watchdog = WatchdogManager(
            db_path=settings.watchdog_db_path,
            run_fn=run,
            memory_driver=drivers.memory,
        )
        _watchdog.start()
    return _watchdog


def _get_or_create_session(session_id: str) -> ContextManager:
    with _sessions_lock:
        if session_id not in _sessions:
            settings = get_settings()
            mgr = ContextManager(
                session_id=session_id,
                window_size=settings.context_window,
            )
            # Hook compactor so evicted entries are summarised into episodic memory
            mgr.on_evict = _get_compactor().handle_eviction
            # Restore persisted context if this session has history
            store = _get_session_store()
            prior = store.load_context(session_id)
            if prior:
                for entry in prior:
                    mgr._buffer.append(entry)
            _sessions[session_id] = mgr
        return _sessions[session_id]


def run(
    raw_input: str,
    session_id: str | None = None,
    confirm_callback: ConfirmCallback | None = None,
    stream_callback: Callable[[str], None] | None = None,
) -> SkillResult:
    """
    Main kernel entry point.

    For simple/atomic requests:
        Router(NANO) → Dispatcher → Skill → SkillResult

    For complex multi-step requests (detected by Planner):
        Router(NANO) → Planner(NANO) → [Step@tier, Step@tier, ...]
                     → each step runs as chat_skill with prior context injected
                     → Combiner(HAIKU) → SkillResult

    Every call is recorded in the audit log automatically.
    Events are emitted on the global bus for any subscriber.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    t_start = time.monotonic()
    drivers = _get_drivers()
    if stream_callback is not None:
        import dataclasses
        drivers = dataclasses.replace(drivers, stream_callback=stream_callback)
    registry = _get_registry()
    audit = _get_audit()
    ctx_manager = _get_or_create_session(session_id)
    context_snapshot = ctx_manager.snapshot()

    bus.emit(Event(
        event_type=Events.KERNEL_RUN_START,
        source="kernel",
        payload={"raw_input": raw_input},
        session_id=session_id,
    ))

    # Route — always NANO (with keyword shortcut + HAIKU retry built in)
    router = Router(llm=drivers.llm, registry=registry)
    intent = router.route(raw_input, context_snapshot)

    # First boot: no IDENTITY.md → force agent_skill for onboarding regardless of router
    if _is_first_boot() and intent.skill_name != "shell_skill":
        from macroa.stdlib.schema import ModelTier as _MT
        intent = Intent(
            raw=intent.raw,
            skill_name="agent_skill",
            parameters=intent.parameters,
            model_tier=intent.model_tier if intent.model_tier != _MT.NANO else _MT.HAIKU,
            routing_confidence=1.0,
            turn_id=intent.turn_id,
        )

    ctx_manager.add_user(turn_id=intent.turn_id, content=raw_input)

    bus.emit(Event(
        event_type=Events.ROUTE_DECISION,
        source="kernel",
        payload={
            "skill": intent.skill_name,
            "confidence": intent.routing_confidence,
            "tier": intent.model_tier.value,
        },
        session_id=session_id,
    ))

    # Agent turns are dispatched directly so confirm_callback + sudo state thread through
    if intent.skill_name == "agent_skill":
        from macroa.kernel.agent import AgentLoop
        loop = AgentLoop(
            drivers=drivers,
            confirm_callback=confirm_callback,
            session_approved=_get_sudo_approved(session_id),
        )
        result = loop.run(intent, context_snapshot)
    else:
        # Plan or single-dispatch for all other skills
        planner = Planner(llm=drivers.llm)
        plan = planner.plan(raw_input, context_snapshot, registry)

        if plan is not None:
            bus.emit(Event(
                event_type=Events.PLAN_CREATED,
                source="kernel",
                payload={"steps": len(plan.steps), "tiers": [s.model_tier.value for s in plan.steps]},
                session_id=session_id,
            ))
            result = _execute_plan(
                raw_input=raw_input,
                plan=plan,
                parent_turn_id=intent.turn_id,
                context_snapshot=context_snapshot,
                registry=registry,
                drivers=drivers,
                planner=planner,
                dispatcher=Dispatcher(registry=registry, drivers=drivers),
            )
        else:
            dispatcher = Dispatcher(registry=registry, drivers=drivers)
            result = dispatcher.dispatch(intent, context_snapshot)

    if not result.turn_id:
        result.turn_id = intent.turn_id

    ctx_manager.add_assistant(result)

    # Persist context so named sessions survive restarts
    _get_session_store().save_context(session_id, list(ctx_manager._buffer))

    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    prompt_tokens, completion_tokens, cost_usd = _compute_cost(drivers.llm.last_usage)

    # Expose token/cost in result metadata so the renderer can show it in debug mode
    if prompt_tokens or completion_tokens:
        result.metadata.update(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )

    # Audit every call
    audit.record(AuditEntry(
        turn_id=intent.turn_id,
        session_id=session_id,
        raw_input=raw_input,
        skill_name=result.metadata.get("skill", intent.skill_name),
        model_tier=result.model_tier.value,
        success=result.success,
        elapsed_ms=elapsed_ms,
        plan_steps=result.metadata.get("plan_steps", 0),
        error=result.error,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
    ))

    bus.emit(Event(
        event_type=Events.KERNEL_RUN_COMPLETE,
        source="kernel",
        payload={
            "turn_id": intent.turn_id,
            "skill": intent.skill_name,
            "tier": result.model_tier.value,
            "success": result.success,
            "elapsed_ms": elapsed_ms,
        },
        session_id=session_id,
    ))

    # Fire-and-forget memory extraction for conversational turns.
    # Runs in a daemon thread — never blocks the response.
    if result.success and intent.skill_name in _EXTRACTABLE_SKILLS and result.output:
        try:
            _get_extractor().extract_async(raw_input, result.output)
        except Exception:
            pass  # extraction is always best-effort

    return result


def _execute_plan(
    raw_input: str,
    plan,
    parent_turn_id: str,
    context_snapshot,
    registry: SkillRegistry,
    drivers: DriverBundle,
    planner: Planner,
    dispatcher: Dispatcher,
) -> SkillResult:
    step_results: list[tuple[str, str]] = []

    for step in plan.steps:
        if step_results:
            prior_context = "\n\n".join(
                f"Previous step ({desc}):\n{output}"
                for desc, output in step_results
            )
            augmented = f"{step.subtask}\n\n[Context from prior steps]\n{prior_context}"
        else:
            augmented = step.subtask

        step_intent = Intent(
            raw=augmented,
            skill_name="chat_skill",
            parameters={},
            model_tier=step.model_tier,
            routing_confidence=1.0,
            turn_id=parent_turn_id,
        )

        step_result = dispatcher.dispatch(step_intent, context_snapshot)
        output = step_result.output if step_result.success else f"[step failed: {step_result.error}]"
        step_results.append((step.subtask, output))
        logger.debug("Plan step done: %s → %d chars", step.subtask[:40], len(output))

    combined = planner.combine(raw_input, step_results, plan.combine_tier)

    return SkillResult(
        output=combined,
        success=True,
        turn_id=parent_turn_id,
        model_tier=plan.combine_tier,
        metadata={
            "skill": "planner",
            "plan_steps": len(plan.steps),
            "step_tiers": [s.model_tier.value for s in plan.steps],
        },
    )


def clear_session(session_id: str) -> None:
    with _sessions_lock:
        if session_id in _sessions:
            _sessions[session_id].clear()
    with _sudo_lock:
        _sudo_approved.pop(session_id, None)
    _get_session_store().save_context(session_id, [])


def get_session_id() -> str:
    return str(uuid.uuid4())


def resolve_session(name: str) -> str:
    """Resolve a human-readable session name → UUID (creates if new)."""
    meta = _get_session_store().get_or_create(name)
    return meta.session_id


def list_sessions():
    """Return list of SessionMeta for all named sessions."""
    return _get_session_store().list_sessions()


def delete_session(name: str) -> bool:
    """Delete a named session and its persisted context."""
    store = _get_session_store()
    rows = store.list_sessions()
    target = next((r for r in rows if r.name == name), None)
    if target:
        with _sessions_lock:
            _sessions.pop(target.session_id, None)
    return store.delete(name)


def schedule_add(label: str, command: str, schedule: str, session_id: str | None = None):
    """Schedule a recurring or one-shot command."""
    sid = session_id or get_session_id()
    return _get_scheduler().add(label=label, command=command, schedule=schedule, session_id=sid)


def schedule_list(include_disabled: bool = False):
    """Return all scheduled tasks."""
    return _get_scheduler().list_tasks(include_disabled=include_disabled)


def schedule_delete(task_id: str) -> bool:
    """Remove a scheduled task by ID."""
    return _get_scheduler().delete(task_id)


def schedule_enable(task_id: str, enabled: bool = True) -> bool:
    """Enable or disable a scheduled task."""
    return _get_scheduler().enable(task_id, enabled)


# ── Watchdog API ──────────────────────────────────────────────────────────────

def watch_add(
    observer_type: str,
    config: dict,
    action: str,
    session_id: str | None = None,
    poll_interval: int = 30,
    once: bool = False,
):
    """Register and start a new watchdog observer."""
    sid = session_id or get_session_id()
    return _get_watchdog().add(
        observer_type=observer_type,
        config=config,
        action=action,
        session_id=sid,
        poll_interval=poll_interval,
        once=once,
    )


def watch_list():
    """Return all registered observers."""
    return _get_watchdog().list_observers()


def watch_delete(observer_id: str) -> bool:
    """Stop and remove an observer."""
    return _get_watchdog().delete(observer_id)


def watch_enable(observer_id: str, enabled: bool = True) -> bool:
    """Enable or disable an observer."""
    return _get_watchdog().enable(observer_id, enabled)


def run_agents(
    tasks: list,
    original_request: str,
    session_id: str | None = None,
) -> SkillResult:
    """Run multiple AgentTask objects in parallel, respecting dependencies.

    Args:
        tasks:            list of AgentTask instances.
        original_request: the top-level user request (used for synthesis prompt).
        session_id:       parent session ID; subagents derive ephemeral sessions from it.

    Returns:
        SkillResult with merged output and metadata.
    """
    from macroa.kernel.multi_agent import MultiAgentCoordinator
    drivers = _get_drivers()
    sid = session_id or get_session_id()
    coordinator = MultiAgentCoordinator(drivers=drivers, session_id=sid)
    return coordinator.run(tasks=tasks, original_request=original_request)


def get_audit_stats() -> dict:
    """Return usage stats from the audit log."""
    return _get_audit().stats()


def shutdown() -> None:
    """Graceful shutdown — stop watchdog, scheduler, heartbeat, teardown tools."""
    global _heartbeat, _tool_registry, _drivers, _session_store, _scheduler, _watchdog
    if _watchdog is not None:
        _watchdog.stop()
    if _scheduler is not None:
        _scheduler.stop()
    if _heartbeat is not None:
        _heartbeat.stop()
    if _tool_registry is not None and _drivers is not None:
        _tool_registry.teardown_all(_drivers)
    if _session_store is not None:
        _session_store.close()
