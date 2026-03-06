"""Agent skill — registered for routing; dispatched directly by the kernel.

The registry requires a callable run() to load this module, but the kernel
bypasses it and dispatches agent turns via AgentLoop directly so that
confirm_callback and session_approved can be threaded through cleanly.
"""

from __future__ import annotations

from macroa.stdlib.schema import Context, DriverBundle, Intent, SkillManifest, SkillResult

MANIFEST = SkillManifest(
    name="agent_skill",
    description=(
        "Use for tasks requiring multiple steps, writing files, running shell commands, "
        "or any action that changes system state. Examples: setting up workspace, "
        "writing identity files, creating scripts, installing packages, organising files, "
        "running a sequence of commands. Do NOT use for simple questions or single-action "
        "memory/file/shell operations — use the dedicated skill for those."
    ),
    triggers=[
        "set up", "create file", "write to", "initialize", "configure",
        "build me", "set up workspace", "onboard", "make a script", "create my",
    ],
    model_tier=None,
    deterministic=False,
)


def run(intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
    # Kernel intercepts agent_skill before reaching here.
    # This stub exists so the skill registry can load the module.
    return SkillResult(
        output="",
        success=False,
        error="agent_skill.run() should never be called directly — kernel dispatch error",
        turn_id=intent.turn_id,
        model_tier=intent.model_tier,
    )
