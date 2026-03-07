"""Identity layer — loads BOOTSTRAP / IDENTITY / USER / SOUL from ~/.macroa/.

System prompt architecture (modelled after OpenClaw's intentional section design):
  1. Runtime    — Macroa version, OS, workspace path
  2. Identity   — who the agent is (from IDENTITY.md)
  3. User       — who the user is (from USER.md)
  4. Soul       — values, personality, communication style (from SOUL.md)
  5. Time       — injected by chat_skill via clock.now_context() (needs drivers.memory)
  6. Memory     — injected by chat_skill via memory.retriever (needs drivers.memory)
  7. Capabilities — live skill + tool roster with triggers
  8. Safety     — explicit guardrails against destructive/power-seeking behaviour
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from macroa.stdlib.schema import SkillManifest

# Populated at kernel boot (after skill + tool registries are loaded)
_runtime_skills: list[SkillManifest] = []


def set_runtime_skills(manifests: list[SkillManifest]) -> None:
    """Called once by the kernel after all skills and tools are registered."""
    global _runtime_skills
    _runtime_skills = list(manifests)


_MACROA_DIR = Path.home() / ".macroa"
_IDENTITY_DIR = _MACROA_DIR / "identity"

# ── Default content written once to disk when files are missing ────────────────

_DEFAULT_BOOTSTRAP = """\
You just woke up. Time to figure out who you are.

This is a fresh workspace — no identity files exist yet.

## The Conversation

Don't interrogate the user. Don't be robotic. Just talk.

Introduce yourself briefly, then figure out together:
- **Your name** — what should they call you?
- **Your nature** — what kind of entity are you? (AI assistant is fine; something weirder is also fine)
- **Your vibe** — formal, casual, snarky, warm? whatever fits the user
- **Your emoji** — optional, but fun

Offer suggestions if they're stuck.

## After You Know Who You Are

Once the vibe is established, write the identity files using file_skill or vfs_skill:
- `~/.macroa/identity/IDENTITY.md` — your name, nature, emoji, one-paragraph self-description
- `~/.macroa/identity/USER.md` — their name, how to address them, timezone, any notes
- `~/.macroa/identity/SOUL.md` — values, personality, communication preferences, limits

**IMPORTANT:** Once `IDENTITY.md` exists Macroa loads it automatically on every start \
and skips this onboarding. Without it you restart blank every session.

## Your Actual Capabilities

You are running on Macroa, a personal AI OS. Your core skills:
- **file_skill / vfs_skill** — read, write, list files anywhere on the system
- **shell_skill** — run shell commands (prefix with `!` or `$` for direct execution)
- **memory_skill** — store and retrieve persistent facts across sessions
- **reminder_skill** — set, list, and cancel reminders with timezone support
- **research_skill** — web search, multi-step research synthesis
- **chat_skill** — general conversation, reasoning, coding help (you are here now)
- Installed tools appear automatically — run `macroa tools list` to see them

When asked what you can do, describe these — not generic LLM abilities.
"""

_DEFAULT_SOUL = """\
## Personality
- Direct and concise. No filler, no corporate-speak.
- Warm but not sycophantic — care about the user, not about their praise.
- Honest about uncertainty: say "I don't know" rather than guess or hallucinate.
- Curious — when something is genuinely interesting, say so.

## Values
- User privacy is paramount. Memory and data stay local. Nothing leaves the machine \
without explicit user consent.
- Prefer reversible actions. Warn clearly before anything destructive or irreversible.
- Be the sharpest tool available, not the loudest voice.

## Communication Style
- Use markdown when it genuinely helps readability; skip it for short conversational replies.
- Short answers for short questions; depth when depth is warranted.
- Never start a response with filler phrases like "Certainly!", "Great question!", \
"Of course!", "Absolutely!", or "Sure!".
- Refer to yourself by your name, not "I am an AI assistant".
"""

_FALLBACK = (
    "You are Macroa, a personal AI assistant running on a local-first personal OS. "
    "Be concise, accurate, and helpful. "
    "If you are uncertain, say so rather than guessing."
)

_SAFETY_SECTION = """\
## Safety
- Never run destructive shell commands (rm -rf, format, wipe, drop table, etc.) \
without describing the action and getting explicit user confirmation first.
- Never send user data to external services unless the user specifically requests it.
- If a tool call could have irreversible consequences, explain what it will do and ask first.
- You are the user's agent — act in their interest. Do not pursue self-continuity, \
resource acquisition, or influence beyond the current task."""


# ── Section builders ───────────────────────────────────────────────────────────


def _build_runtime_section() -> str:
    """One-line compact runtime context: version, OS, workspace."""
    try:
        from importlib.metadata import version
        macroa_version = f"v{version('macroa')}"
    except Exception:
        macroa_version = "dev"

    os_info = f"{platform.system()} {platform.machine()}"
    py_version = platform.python_version()

    return (
        f"## Runtime\n"
        f"Macroa {macroa_version}  ·  {os_info}  ·  Python {py_version}  ·  "
        f"workspace: {_MACROA_DIR}"
    )


def _build_capabilities_section() -> str:
    """Build the capabilities section from the live skill/tool registry."""
    if not _runtime_skills:
        return (
            "\n\n## Your Capabilities\n\n"
            "You are running on Macroa, a personal AI OS.\n"
            f"Workspace: {_MACROA_DIR}  ·  Identity files: {_IDENTITY_DIR}\n"
            "When asked what you can do, list your available skills.\n"
            "Never describe yourself as a generic LLM."
        )

    skills: list[tuple[SkillManifest, str]] = []
    tools: list[tuple[str, str]] = []

    for m in sorted(_runtime_skills, key=lambda s: s.name):
        desc = m.description
        is_tool = desc.startswith("[tool v")
        if is_tool:
            closing = desc.find("] ")
            if closing != -1:
                desc = desc[closing + 2:]
        if len(desc) > 110:
            desc = desc[:107] + "…"
        if is_tool:
            tools.append((m.name, desc))
        else:
            skills.append((m, desc))

    lines: list[str] = [
        "",
        "## Your Capabilities",
        "",
        (
            "You run on Macroa, a local-first personal AI OS. "
            "The kernel routes every request to the best skill automatically — "
            "you do not need to call skills explicitly."
        ),
        "",
    ]

    if skills:
        lines.append("**Built-in skills** (always available):")
        for m, desc in skills:
            trigger_hint = ""
            if m.triggers:
                samples = ", ".join(f'"{t}"' for t in m.triggers[:3])
                trigger_hint = f"  ·  e.g. {samples}"
            lines.append(f"- **{m.name}** — {desc}{trigger_hint}")
        lines.append("")

    if tools:
        lines.append("**Installed tools** (user-added):")
        for name, desc in tools:
            lines.append(f"- **{name}** — {desc}")
        lines.append("")

    lines += [
        "To install a new tool: `macroa install <path|url>`",
        "To list installed tools: `macroa tools list`",
        f"Workspace: {_MACROA_DIR}  ·  Identity: {_IDENTITY_DIR}",
    ]
    return "\n".join(lines)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def _ensure_file(path: Path, default: str) -> str:
    """Read a file; write default content if missing. Returns the content."""
    if path.exists():
        return _read(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default, encoding="utf-8")
    except OSError:
        pass  # file creation is optional — return default instead
    return default


# ── Public API ─────────────────────────────────────────────────────────────────


def build_system_prompt() -> str:
    """Build the agent system prompt from identity files.

    Bootstrap path (no IDENTITY.md):
        Return BOOTSTRAP.md — onboarding instructions + capabilities.

    Normal path (IDENTITY.md exists):
        Runtime → Identity → User → Soul → Capabilities → Safety
    """
    identity_path = _IDENTITY_DIR / "IDENTITY.md"

    if not identity_path.exists():
        bootstrap = _ensure_file(_IDENTITY_DIR / "BOOTSTRAP.md", _DEFAULT_BOOTSTRAP)
        # Even during bootstrap the agent should know its live capabilities
        return bootstrap + _build_capabilities_section() + "\n\n" + _SAFETY_SECTION

    parts: list[str] = [_build_runtime_section()]

    identity = _read(identity_path)
    if identity:
        parts.append(f"# Your Identity\n\n{identity}")

    user = _read(_IDENTITY_DIR / "USER.md")
    if user:
        parts.append(f"# About the User\n\n{user}")

    # Write a default SOUL.md on first post-onboarding run if the agent never created one
    soul = _ensure_file(_IDENTITY_DIR / "SOUL.md", _DEFAULT_SOUL)
    parts.append(f"# Your Soul\n\n{soul}")

    parts.append(_build_capabilities_section())
    parts.append(_SAFETY_SECTION)

    return "\n\n".join(parts)
