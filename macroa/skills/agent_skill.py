"""Agent skill — registered for routing; dispatched directly by the kernel."""

from __future__ import annotations

from macroa.stdlib.schema import SkillManifest

# MANIFEST is all the registry needs — kernel dispatches agent turns directly
# via AgentLoop rather than calling run() here, so that confirm_callback and
# session_approved can be threaded in without polluting the skill interface.
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
