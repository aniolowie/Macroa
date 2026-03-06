"""Identity layer — loads BOOTSTRAP / IDENTITY / USER / SOUL from ~/.macroa/."""

from __future__ import annotations

from pathlib import Path

_MACROA_DIR = Path.home() / ".macroa"

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

## After You Know Who You Are

Tell the user to create these files so you remember next time:
- ~/.macroa/IDENTITY.md — your name, creature type, vibe, emoji
- ~/.macroa/USER.md — their name, how to address them, timezone, notes
- ~/.macroa/SOUL.md — what matters to them, how they want you to behave, any limits

Once those files exist, you'll load them automatically on every startup.
"""

_FALLBACK = (
    "You are Macroa, a personal AI assistant. "
    "Be concise, accurate, and helpful. "
    "If you are uncertain, say so rather than guessing."
)


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
    identity_path = _MACROA_DIR / "IDENTITY.md"

    if not identity_path.exists():
        bootstrap_path = _MACROA_DIR / "BOOTSTRAP.md"
        if not bootstrap_path.exists():
            try:
                _MACROA_DIR.mkdir(parents=True, exist_ok=True)
                bootstrap_path.write_text(_DEFAULT_BOOTSTRAP, encoding="utf-8")
            except OSError:
                pass
        content = _read(bootstrap_path)
        return content if content else _DEFAULT_BOOTSTRAP

    parts: list[str] = []

    identity = _read(identity_path)
    if identity:
        parts.append(f"# Your Identity\n{identity}")

    user = _read(_MACROA_DIR / "USER.md")
    if user:
        parts.append(f"# About the User\n{user}")

    soul = _read(_MACROA_DIR / "SOUL.md")
    if soul:
        parts.append(f"# Your Soul\n{soul}")

    return "\n\n".join(parts) if parts else _FALLBACK
