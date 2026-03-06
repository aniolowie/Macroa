"""Public kernel API — kernel.run(input, session_id) is the only entry point."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from macroa.config.settings import get_settings
from macroa.config.skill_registry import SkillRegistry
from macroa.drivers.fs_driver import FSDriver
from macroa.drivers.llm_driver import LLMDriver
from macroa.drivers.memory_driver import MemoryDriver
from macroa.drivers.network_driver import NetworkDriver
from macroa.drivers.shell_driver import ShellDriver
from macroa.kernel.audit import AuditEntry, AuditLog
from macroa.kernel.context import ContextManager
from macroa.kernel.dispatcher import Dispatcher
from macroa.kernel.events import Event, EventBus, Events, bus
from macroa.kernel.planner import Planner
from macroa.kernel.router import Router
from macroa.kernel.sessions import SessionStore
from macroa.stdlib.schema import DriverBundle, Intent, ModelTier, SkillResult
from macroa.tools.heartbeat import HeartbeatManager
from macroa.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Thread-safe session store: session_id → ContextManager (in-process cache)
_sessions: dict[str, ContextManager] = {}
_sessions_lock = threading.Lock()

# Module-level singletons (lazy-initialized)
_drivers: DriverBundle | None = None
_registry: SkillRegistry | None = None
_tool_registry: ToolRegistry | None = None
_heartbeat: HeartbeatManager | None = None
_audit: AuditLog | None = None
_session_store: SessionStore | None = None


def _get_drivers() -> DriverBundle:
    global _drivers
    if _drivers is None:
        settings = get_settings()
        _drivers = DriverBundle(
            llm=LLMDriver(
                api_key=settings.openrouter_api_key,
                model_map=settings.model_map,
                http_referer=settings.http_referer,
                app_title=settings.app_title,
            ),
            shell=ShellDriver(),
            fs=FSDriver(),
            memory=MemoryDriver(
                backend=settings.memory_backend,
                db_path=settings.memory_db_path,
            ),
            network=NetworkDriver(timeout=settings.network_timeout),
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
        _tool_registry = ToolRegistry()
        _tool_registry.load_from_dir(settings.builtin_tools_dir, drivers)
        _tool_registry.load_from_dir(settings.tools_dir, drivers)
        _tool_registry.inject_into(_registry)

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


def _get_or_create_session(session_id: str) -> ContextManager:
    with _sessions_lock:
        if session_id not in _sessions:
            settings = get_settings()
            mgr = ContextManager(
                session_id=session_id,
                window_size=settings.context_window,
            )
            # Restore persisted context if this session has history
            store = _get_session_store()
            prior = store.load_context(session_id)
            if prior:
                for entry in prior:
                    mgr._buffer.append(entry)
            _sessions[session_id] = mgr
        return _sessions[session_id]


def run(raw_input: str, session_id: str | None = None) -> SkillResult:
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

    # Route — always NANO
    router = Router(llm=drivers.llm, registry=registry)
    intent = router.route(raw_input, context_snapshot)
    ctx_manager.add_user(turn_id=intent.turn_id, content=raw_input)

    # Plan or single-dispatch
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


def get_audit_stats() -> dict:
    """Return usage stats from the audit log."""
    return _get_audit().stats()


def shutdown() -> None:
    """Graceful shutdown — stop heartbeat, teardown tools, close session store."""
    global _heartbeat, _tool_registry, _drivers, _session_store
    if _heartbeat is not None:
        _heartbeat.stop()
    if _tool_registry is not None and _drivers is not None:
        _tool_registry.teardown_all(_drivers)
    if _session_store is not None:
        _session_store.close()
