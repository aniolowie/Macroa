"""File skill — deterministic reads/writes via the FS driver."""

from __future__ import annotations

from macroa.drivers.fs_driver import FSDriverError
from macroa.stdlib.schema import (
    Context,
    DriverBundle,
    Intent,
    SkillManifest,
    SkillResult,
)

MANIFEST = SkillManifest(
    name="file_skill",
    description=(
        "Reads or writes files on the local filesystem. "
        "Use when the user wants to read a file, write content to a file, "
        "list a directory, or check if a file exists. "
        "Parameters: action (read|write|list|exists), path, content (for write)."
    ),
    triggers=["read file", "write file", "open file", "save file", "list directory", "cat", "show file"],
    model_tier=None,
    deterministic=True,
)


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    action = intent.parameters.get("action", "read").lower()
    path = intent.parameters.get("path", "").strip()

    if not path:
        return SkillResult(
            output="",
            success=False,
            error="No path provided to file_skill",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )

    try:
        if action == "read":
            content = drivers.fs.read(path)
            return SkillResult(
                output=content,
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "read", "path": path},
            )

        elif action == "write":
            file_content = intent.parameters.get("content", "")
            written_path = drivers.fs.write(path, file_content)
            return SkillResult(
                output=f"Written to {written_path}",
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "write", "path": str(written_path)},
            )

        elif action == "list":
            entries = drivers.fs.list_dir(path)
            output = "\n".join(entries) if entries else "(empty directory)"
            return SkillResult(
                output=output,
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "list", "path": path, "count": len(entries)},
            )

        elif action == "exists":
            exists = drivers.fs.exists(path)
            return SkillResult(
                output=f"{'Exists' if exists else 'Does not exist'}: {path}",
                success=True,
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
                metadata={"action": "exists", "path": path, "exists": exists},
            )

        else:
            return SkillResult(
                output="",
                success=False,
                error=f"Unknown file action: {action!r}. Use read, write, list, or exists.",
                turn_id=intent.turn_id,
                model_tier=intent.model_tier,
            )

    except FSDriverError as exc:
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
            error=f"file_skill unexpected error: {exc}",
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )
