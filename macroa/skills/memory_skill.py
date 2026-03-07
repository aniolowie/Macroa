"""Memory skill — deterministic store/retrieve via the memory driver."""

from __future__ import annotations

from macroa.stdlib.schema import (
    Context,
    DriverBundle,
    Intent,
    SkillManifest,
    SkillResult,
)

MANIFEST = SkillManifest(
    name="memory_skill",
    description=(
        "Stores and retrieves facts from the user's persistent memory. "
        "Use when the user says 'remember', 'save', 'store', asks to recall "
        "something they previously told the system, asks what the assistant knows "
        "about them, or wants to pin/unpin a memory. "
        "Parameters: action (set|get|search|list|delete|pin|unpin), "
        "namespace (default: 'user'), key, value (for set), query (for search)."
    ),
    triggers=[
        "remember", "recall", "save", "store", "forget",
        "memory", "note that", "keep in mind", "what do you know",
        "what you know", "pin", "unpin",
    ],
    model_tier=None,
    deterministic=True,
)

_DEFAULT_NS = "user"

# Phrases that mean "show me everything you know about me"
_LIST_ALL_SIGNALS = frozenset([
    "about me", "know about me", "what you know", "what do you know",
    "tell me what you know", "show me what you know", "you know about me",
    "memory", "memories",
    "describe me", "describe who", "who am i", "who i am",
    "tell me about me", "tell me about myself", "about myself",
    "what i told", "what have i told", "what do i",
])


def _is_list_all_query(query: str) -> bool:
    q = query.lower()
    return any(signal in q for signal in _LIST_ALL_SIGNALS)


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    action = intent.parameters.get("action", "search").lower()
    namespace = intent.parameters.get("namespace", _DEFAULT_NS)

    try:
        if action == "set":
            return _action_set(intent, namespace, drivers)

        elif action == "get":
            return _action_get(intent, namespace, drivers)

        elif action == "search":
            query = intent.parameters.get("query", intent.raw).strip()
            # Redirect vague "what do you know about me" queries to list_all
            if _is_list_all_query(query):
                return _action_list(intent, namespace, drivers)
            return _action_search(intent, namespace, query, drivers)

        elif action == "list":
            return _action_list(intent, namespace, drivers)

        elif action == "delete":
            return _action_delete(intent, namespace, drivers)

        elif action in ("pin", "unpin"):
            return _action_pin(intent, namespace, action == "pin", drivers)

        else:
            return SkillResult(
                output="",
                success=False,
                error=(
                    f"Unknown memory action: {action!r}. "
                    "Use: set, get, search, list, delete, pin, unpin."
                ),
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
            )

    except Exception as exc:
        return SkillResult(
            output="",
            success=False,
            error=f"memory_skill unexpected error: {exc}",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )


# ------------------------------------------------------------------ action helpers

def _action_set(intent: Intent, namespace: str, drivers: DriverBundle) -> SkillResult:
    key = intent.parameters.get("key", "").strip()
    value = intent.parameters.get("value", "").strip()
    if not key or not value:
        return SkillResult(
            output="",
            success=False,
            error="memory_skill set requires both 'key' and 'value'",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
    pinned = bool(intent.parameters.get("pinned", False))
    drivers.memory.set_fact(namespace, key, value, source="user", pinned=pinned)
    pin_note = " (pinned)" if pinned else ""
    return SkillResult(
        output=f"Remembered: {key} = {value}{pin_note}",
        success=True,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={"action": "set", "namespace": namespace, "key": key},
    )


def _action_get(intent: Intent, namespace: str, drivers: DriverBundle) -> SkillResult:
    key = intent.parameters.get("key", "").strip()
    if not key:
        return SkillResult(
            output="",
            success=False,
            error="memory_skill get requires 'key'",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
    value = drivers.memory.get(namespace, key)
    if value is None:
        return SkillResult(
            output=f"No memory found for key: {key}",
            success=True,
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
            metadata={"action": "get", "found": False},
        )
    return SkillResult(
        output=f"{key} = {value}",
        success=True,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={"action": "get", "found": True, "key": key, "value": value},
    )


def _action_search(
    intent: Intent, namespace: str, query: str, drivers: DriverBundle
) -> SkillResult:
    ns_filter = namespace if namespace != _DEFAULT_NS else None
    results = drivers.memory.search_fts(query, limit=15)
    if ns_filter:
        results = [r for r in results if r["namespace"] == ns_filter]
    if not results:
        # FTS came up empty — fall back to LIKE
        results = drivers.memory.search(query, namespace=ns_filter)
    if not results:
        return SkillResult(
            output=f"No memories found matching: {query}",
            success=True,
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
            metadata={"action": "search", "count": 0},
        )
    lines = [f"• {'📌 ' if r.get('pinned') else ''}{r['key']} = {r['value']}" for r in results]
    return SkillResult(
        output="\n".join(lines),
        success=True,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={"action": "search", "count": len(results)},
    )


def _action_list(intent: Intent, namespace: str, drivers: DriverBundle) -> SkillResult:
    results = drivers.memory.list_all(namespace=namespace)
    if not results:
        return SkillResult(
            output="I don't have any memories stored yet.",
            success=True,
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
            metadata={"action": "list", "count": 0},
        )
    lines = [
        f"• {'📌 ' if r.get('pinned') else ''}[{r['namespace']}] {r['key']} = {r['value']}"
        for r in results
    ]
    return SkillResult(
        output="\n".join(lines),
        success=True,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={"action": "list", "count": len(results)},
    )


def _action_delete(intent: Intent, namespace: str, drivers: DriverBundle) -> SkillResult:
    key = intent.parameters.get("key", "").strip()
    if not key:
        return SkillResult(
            output="",
            success=False,
            error="memory_skill delete requires 'key'",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
    deleted = drivers.memory.delete(namespace, key)
    return SkillResult(
        output=f"{'Deleted' if deleted else 'Key not found'}: {key}",
        success=True,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={"action": "delete", "deleted": deleted},
    )


def _action_pin(
    intent: Intent, namespace: str, pin: bool, drivers: DriverBundle
) -> SkillResult:
    key = intent.parameters.get("key", "").strip()
    if not key:
        return SkillResult(
            output="",
            success=False,
            error=f"memory_skill {('pin' if pin else 'unpin')} requires 'key'",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
    ok = drivers.memory.pin(namespace, key, pinned=pin)
    verb = "Pinned" if pin else "Unpinned"
    return SkillResult(
        output=f"{verb}: {key}" if ok else f"Key not found: {key}",
        success=True,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={"action": "pin" if pin else "unpin", "key": key, "found": ok},
    )
