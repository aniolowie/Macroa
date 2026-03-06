"""Memory skill — deterministic store/retrieve via the memory driver."""

from __future__ import annotations

from macroa.stdlib.schema import (
    Context, DriverBundle, Intent, SkillManifest, SkillResult,
)

MANIFEST = SkillManifest(
    name="memory_skill",
    description=(
        "Stores and retrieves facts from the user's persistent memory. "
        "Use when the user says 'remember', 'save', 'store', or asks to recall "
        "something they previously told the system. "
        "Parameters: action (set|get|search|delete|list), namespace (default: 'user'), "
        "key, value (for set), query (for search)."
    ),
    triggers=[
        "remember", "recall", "save", "store", "forget", "what is my", "what's my",
        "memory", "note that", "keep in mind",
    ],
    model_tier=None,
    deterministic=True,
)

_DEFAULT_NS = "user"


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    action = intent.parameters.get("action", "search").lower()
    namespace = intent.parameters.get("namespace", _DEFAULT_NS)

    try:
        if action == "set":
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
            drivers.memory.set(namespace, key, value)
            return SkillResult(
                output=f"Remembered: {key} = {value}",
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "set", "namespace": namespace, "key": key},
            )

        elif action == "get":
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

        elif action == "search":
            query = intent.parameters.get("query", intent.raw).strip()
            results = drivers.memory.search(query, namespace=namespace if namespace != _DEFAULT_NS else None)
            if not results:
                return SkillResult(
                    output=f"No memories found matching: {query}",
                    success=True,
                    turn_id=intent.turn_id,
                    model_tier=intent.model_tier,
                    metadata={"action": "search", "count": 0},
                )
            lines = [f"• {r['key']} = {r['value']}" for r in results]
            return SkillResult(
                output="\n".join(lines),
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "search", "count": len(results)},
            )

        elif action == "delete":
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

        elif action == "list":
            results = drivers.memory.list_all(namespace=namespace)
            if not results:
                return SkillResult(
                    output="Memory is empty.",
                    success=True,
                    turn_id=intent.turn_id,
                    model_tier=intent.model_tier,
                    metadata={"action": "list", "count": 0},
                )
            lines = [f"• [{r['namespace']}] {r['key']} = {r['value']}" for r in results]
            return SkillResult(
                output="\n".join(lines),
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "list", "count": len(results)},
            )

        else:
            return SkillResult(
                output="",
                success=False,
                error=f"Unknown memory action: {action!r}. Use set, get, search, delete, or list.",
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
