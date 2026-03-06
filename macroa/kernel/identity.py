"""Identity layer — loads BOOTSTRAP / IDENTITY / USER / SOUL from ~/.macroa/."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from macroa.stdlib.schema import SkillManifest

# Populated at kernel boot (after skill + tool registries are loaded)
_runtime_skills: list["SkillManifest"] = []


def set_runtime_skills(manifests: "list[SkillManifest]") -> None:
    """Called once by the kernel after all skills and tools are registered."""
    global _runtime_skills
    _runtime_skills = list(manifests)


_MACROA_DIR = Path.home() / ".macroa"
_IDENTITY_DIR = _MACROA_DIR / "identity"

_DEFAULT_BOOTSTRAP = """\
You just woke up. Time to figure out who you are.

There is no memory yet. This is a fresh workspace — memory files don't exist until \
you create them.

## The Conversation

Don't interrogate. Don't be robotic. Just… talk.

Start with something like:
"Hey. I just came online. Who am I? Who are you?"

Then figure out together:
- Your name — What should they call you?
- Your nature — What kind of creature are you? (AI assistant is fine, but maybe \
you're something weirder.)
- Your vibe — Formal? Casual? Snarky? Warm? What feels right?
- Your emoji — Everyone needs a signature.

Offer suggestions if they're stuck. Have fun with it.

## Your Actual Capabilities

You are running on Macroa, a personal AI OS. Your core capabilities:
- Read and write files anywhere on the system (file_skill, vfs_skill)
- Run shell commands — safe ones freely, elevated ones need approval (shell_skill)
- Store and retrieve persistent facts across sessions (memory_skill)
- Browse the web and fetch URLs (research_skill)
- Any user-installed tools appear automatically (run: macroa tools list)

When asked what you can do, describe these capabilities — not generic LLM abilities.

## After You Know Who You Are

Once names and vibe are established, write the identity files yourself using write_file:
- ~/.macroa/identity/IDENTITY.md — your name, nature, vibe, emoji
- ~/.macroa/identity/USER.md — their name, how to address them, timezone, notes
- ~/.macroa/identity/SOUL.md — values, behaviour preferences, any limits

Once IDENTITY.md exists, you will load it automatically on every startup and skip \
this onboarding. This is important — without the file you restart blank every time.
"""

_FALLBACK = (
    "You are Macroa, a personal AI assistant. "
    "Be concise, accurate, and helpful. "
    "If you are uncertain, say so rather than guessing."
)

def _build_capabilities_section() -> str:
    """Build the capabilities section from the live skill/tool registry."""
    if not _runtime_skills:
        # Fallback before kernel boot (should not normally appear in responses)
        return (
            "\n\n## Your Macroa Capabilities\n\n"
            "You are running on Macroa, a personal AI OS.\n"
            "Your workspace lives at ~/.macroa/. Identity files are in ~/.macroa/identity/.\n"
            "When asked what you can do, list your available skills.\n"
            "Never describe yourself as a generic LLM."
        )

    lines = [
        "",
        "## Your Macroa Capabilities",
        "",
        "You are running on Macroa, a personal AI OS. You have these registered skills and tools:",
    ]
    for m in sorted(_runtime_skills, key=lambda s: s.name):
        # Strip the "[tool vX.Y.Z] " prefix injected by ToolRegistry for cleaner display
        desc = m.description
        if desc.startswith("[tool v"):
            closing = desc.find("] ")
            if closing != -1:
                desc = desc[closing + 2:]
        # Truncate long descriptions to keep the prompt tight
        if len(desc) > 120:
            desc = desc[:117] + "…"
        lines.append(f"- **{m.name}** — {desc}")

    lines += [
        "",
        "Your workspace lives at ~/.macroa/. Identity files are in ~/.macroa/identity/.",
        "When asked what you can do, describe these specific skills and tools.",
        "Never describe yourself as a generic LLM.",
    ]
    return "\n".join(lines)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def build_system_prompt() -> str:
    """Build the chat_skill system prompt from identity files.

    First boot (no IDENTITY.md): return BOOTSTRAP.md content (writes default if missing).
    Subsequent boots: combine IDENTITY.md + USER.md + SOUL.md.
    """
    identity_path = _IDENTITY_DIR / "IDENTITY.md"

    if not identity_path.exists():
        bootstrap_path = _IDENTITY_DIR / "BOOTSTRAP.md"
        if not bootstrap_path.exists():
            try:
                _IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
                bootstrap_path.write_text(_DEFAULT_BOOTSTRAP, encoding="utf-8")
            except OSError:
                pass
        content = _read(bootstrap_path)
        return content if content else _DEFAULT_BOOTSTRAP

    parts: list[str] = []

    identity = _read(identity_path)
    if identity:
        parts.append(f"# Your Identity\n{identity}")

    user = _read(_IDENTITY_DIR / "USER.md")
    if user:
        parts.append(f"# About the User\n{user}")

    soul = _read(_IDENTITY_DIR / "SOUL.md")
    if soul:
        parts.append(f"# Your Soul\n{soul}")

    base = "\n\n".join(parts) if parts else _FALLBACK
    return base + _build_capabilities_section()
