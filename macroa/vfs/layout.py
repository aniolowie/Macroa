"""Canonical ~/.macroa/ directory layout — single source of truth for the agent's home.

Every subdirectory has a declared purpose. The bootstrap function creates missing
directories and migrates legacy flat files to the new structure on first run.
The watchdog (future) will diff the live tree against this layout to detect drift.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

MACROA_DIR = Path.home() / ".macroa"

# Ordered dict: subdir → human-readable purpose.
# Order matters — parents before children.
LAYOUT: dict[str, str] = {
    "identity":          "Agent identity and user profile (IDENTITY.md, USER.md, SOUL.md)",
    "memory":            "Persistent knowledge databases",
    "workspace":         "Agent working area — structured freedom",
    "workspace/scratch": "Ephemeral scratch space — agent writes freely, no guarantees",
    "workspace/output":  "Finished artifacts produced by the agent",
    "research":          "Research reports from research_skill",
    "tools":             "User-installed tools",
    "logs":              "Audit trail and scheduler state",
    "sessions":          "Named session storage",
    "config":            "Runtime configuration",
}

# Files that moved from flat ~/.macroa/ to subdirectories in this layout.
# Migration is non-destructive: copy src → dst only when dst doesn't exist yet.
_MIGRATIONS: list[tuple[str, str]] = [
    ("IDENTITY.md",  "identity/IDENTITY.md"),
    ("USER.md",      "identity/USER.md"),
    ("SOUL.md",      "identity/SOUL.md"),
    ("BOOTSTRAP.md", "identity/BOOTSTRAP.md"),
    ("memory.db",    "memory/memory.db"),
    ("audit.db",     "logs/audit.db"),
    ("sessions.db",  "sessions/sessions.db"),
    ("scheduler.db", "logs/scheduler.db"),
]


def bootstrap_layout() -> None:
    """Ensure the canonical ~/.macroa/ tree exists. Migrate legacy flat files.

    Safe to call multiple times — idempotent.
    """
    MACROA_DIR.mkdir(parents=True, exist_ok=True)
    for subdir in LAYOUT:
        (MACROA_DIR / subdir).mkdir(parents=True, exist_ok=True)
    _migrate_legacy()


def _migrate_legacy() -> None:
    """Move flat files from old layout to new subdirectory layout (non-destructive)."""
    for old, new in _MIGRATIONS:
        src = MACROA_DIR / old
        dst = MACROA_DIR / new
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            logger.info("VFS layout: migrated %s → %s", old, new)


def layout_status() -> dict[str, bool]:
    """Return {subdir: exists} for every declared directory. Entry point for watchdog."""
    return {subdir: (MACROA_DIR / subdir).is_dir() for subdir in LAYOUT}
