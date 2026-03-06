"""Shell skill — deterministic, executes shell commands directly."""

from __future__ import annotations

from macroa.stdlib.schema import (
    Context,
    DriverBundle,
    Intent,
    SkillManifest,
    SkillResult,
)

MANIFEST = SkillManifest(
    name="shell_skill",
    description=(
        "Executes shell commands on the local system. "
        "Use when the user wants to run a command, check system status, "
        "list files, check processes, or perform any OS-level operation. "
        "Input always starts with '!' or '$' for hard routing."
    ),
    triggers=["!", "$", "run command", "execute", "shell", "bash", "ls", "pwd", "ps"],
    model_tier=None,
    deterministic=True,
)


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    command = intent.parameters.get("command", "").strip()
    if not command:
        return SkillResult(
            output="What command would you like me to run? (e.g. ! ls -la  or  ! df -h)",
            success=True,
            turn_id=intent.turn_id,
            model_tier=intent.model_tier,
        )

    exit_code, stdout, stderr = drivers.shell.run(command)
    success = exit_code == 0
    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if not parts:
        parts.append(f"(exited with code {exit_code})")

    output = "\n".join(parts)
    error = None if success else f"Exit code {exit_code}"

    return SkillResult(
        output=output,
        success=success,
        error=error,
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
        metadata={"exit_code": exit_code, "command": command},
    )
