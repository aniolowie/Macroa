"""VFS skill — unified read/write/list/delete interface over the agent filesystem.

Replaces the split between file_skill (local files) and memory_skill (SQLite facts)
with a single skill that speaks VFS paths. Every resource in the agent's OS is
addressable here — no knowledge of what backend is behind a path is required.

VFS path examples:
    /identity/IDENTITY.md       — the agent's identity file
    /identity/USER.md           — user profile
    /workspace/scratch/notes.txt — ephemeral working file
    /workspace/output/report.md  — finished artifact
    /research/2026-03-06-lol.md  — a research report
    /mem/user/ip_address         — a stored memory fact
    /mem/project/goal            — project-scoped memory
    /fs/etc/hosts                — full filesystem access (elevated intent)
"""

from __future__ import annotations

from macroa.stdlib.schema import (
    Context,
    DriverBundle,
    Intent,
    SkillManifest,
    SkillResult,
)
from macroa.vfs import VFSError

MANIFEST = SkillManifest(
    name="vfs_skill",
    description=(
        "Read, write, list, delete, or inspect any resource in the agent's unified filesystem. "
        "Use VFS paths: /identity/ for identity files, /workspace/ for working files, "
        "/research/ for reports, /mem/<namespace>/<key> for persistent facts, "
        "/fs/<path> for system files. "
        "Parameters: action (read|write|list|delete|exists|stat), path, content (for write)."
    ),
    triggers=[
        "read /", "write /", "list /", "show /identity", "show /workspace",
        "show /research", "show /mem", "/mem/", "/identity/", "/workspace/",
    ],
    model_tier=None,
    deterministic=True,
)


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    vfs = drivers.vfs
    if vfs is None:
        return SkillResult(
            output="",
            success=False,
            error="VFS not available — kernel not initialised",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )

    action = intent.parameters.get("action", "read").lower()
    path = intent.parameters.get("path", "").strip()

    if not path:
        return SkillResult(
            output="",
            success=False,
            error="No path provided to vfs_skill",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )

    try:
        if action == "read":
            content = vfs.read(path)
            return SkillResult(
                output=content,
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "read", "path": path},
            )

        elif action == "write":
            content = intent.parameters.get("content", "")
            written = vfs.write(path, content)
            return SkillResult(
                output=f"Written to {written}",
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "write", "path": path},
            )

        elif action == "list":
            entries = vfs.list(path)
            output = "\n".join(entries) if entries else "(empty)"
            return SkillResult(
                output=output,
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "list", "path": path, "count": len(entries)},
            )

        elif action == "exists":
            exists = vfs.exists(path)
            return SkillResult(
                output=f"{'Exists' if exists else 'Does not exist'}: {path}",
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "exists", "path": path, "exists": exists},
            )

        elif action == "delete":
            deleted = vfs.delete(path)
            return SkillResult(
                output=f"{'Deleted' if deleted else 'Not found'}: {path}",
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "delete", "path": path, "deleted": deleted},
            )

        elif action == "stat":
            node = vfs.stat(path)
            if node is None:
                return SkillResult(
                    output=f"Not found: {path}",
                    success=True,
                    turn_id=intent.turn_id,
                    model_tier=intent.model_tier,
                    metadata={"action": "stat", "found": False},
                )
            kind = "dir" if node.is_dir else "file"
            size_str = f", {node.size} bytes" if node.size is not None else ""
            return SkillResult(
                output=f"{path} [{kind}{size_str}] (backend: {node.backend})",
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "stat", "found": True, "is_dir": node.is_dir},
            )

        else:
            return SkillResult(
                output="",
                success=False,
                error=f"Unknown action: {action!r}. Use read, write, list, exists, delete, stat.",
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
            )

    except (FileNotFoundError, IsADirectoryError, ValueError, VFSError) as exc:
        return SkillResult(
            output="",
            success=False,
            error=str(exc),
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
    except Exception as exc:
        return SkillResult(
            output="",
            success=False,
            error=f"vfs_skill unexpected error: {exc}",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
