"""Command permission classifier — SAFE / ELEVATED / BLOCKED.

Three tiers:
  SAFE     — runs immediately, no prompt
  ELEVATED — agent pauses, user must confirm within 30 s or it auto-denies
  BLOCKED  — rejected outright, never executes

Session allowlist: once the user approves a pattern type (e.g. "rm") for
a session, subsequent commands matching the same pattern auto-approve.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path


class CommandLevel(Enum):
    SAFE = "safe"
    ELEVATED = "elevated"
    BLOCKED = "blocked"


# ── Blocked forever ──────────────────────────────────────────────────────────
_BLOCKED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-[rRfF]*[rR][fF]?\s+/"), "would delete root filesystem"),
    (re.compile(r"\brm\s+-[rRfF]*[fF][rR]?\s+/"), "would delete root filesystem"),
    (re.compile(r"\bdd\b.+\bof=/dev/(sd|hd|nvme|vd|xvd)\w*"), "writes to block device"),
    (re.compile(r"\bmkfs\b"), "formats a filesystem"),
    (re.compile(r":\(\)\s*\{"), "fork bomb pattern"),
    (re.compile(r"(curl|wget)\b.+\|\s*(ba)?sh\b"), "executes remote code via shell pipe"),
    (re.compile(r">\s*(/etc/passwd|/etc/shadow|/etc/sudoers)"), "overwrites auth files"),
]

# ── Require user confirmation ─────────────────────────────────────────────────
# Each entry: (compiled pattern, human-readable reason, pattern_key for allowlist)
_ELEVATED: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\brm(dir)?\b"),                                       "deletes files or directories",          "rm"),
    (re.compile(r"\bmv\b"),                                             "moves or overwrites files",             "mv"),
    (re.compile(r"(?<![>])>(?![>])"),                                   "overwrites file contents",              "redirect_overwrite"),
    (re.compile(r"\b(chmod|chown)\b"),                                  "changes file permissions/ownership",    "chmod"),
    (re.compile(r"\b(kill|killall|pkill)\b"),                           "terminates processes",                  "kill"),
    (re.compile(r"\b(pip|pip3)\s+(install|uninstall)\b"),               "modifies Python packages",              "pip"),
    (re.compile(r"\bapt(-get)?\s+(install|remove|purge|autoremove)\b"), "modifies system packages",              "apt"),
    (re.compile(r"\bbrew\s+(install|uninstall|remove)\b"),              "modifies Homebrew packages",            "brew"),
    (re.compile(r"\bnpm\s+(install|uninstall|remove|ci)\b"),            "modifies npm packages",                 "npm"),
    (re.compile(r"\bgit\s+(push|reset|clean|rebase)\b"),                "destructive git operation",             "git_destructive"),
    (re.compile(r"\bcrontab\b"),                                        "modifies cron jobs",                    "crontab"),
    (re.compile(r"\bsystemctl\s+(start|stop|restart|enable|disable)\b"),"manages system services",               "systemctl"),
    (re.compile(r"\bsudo\b"),                                           "privilege escalation inside agent",     "sudo"),
    (re.compile(r"\b(chpasswd|passwd)\b"),                              "changes passwords",                     "passwd"),
]


def _all_targets_tmp(command: str) -> bool:
    """Return True if every path-like token in the command is under /tmp/."""
    paths = re.findall(r"[~/][\w./\-]+", command)
    if not paths:
        return False
    return all(p.startswith("/tmp/") or p.startswith("~/tmp/") for p in paths)


def classify(command: str) -> tuple[CommandLevel, str, str]:
    """Classify a shell command.

    Returns (level, human_reason, pattern_key).
    pattern_key is used for the session allowlist — empty string for SAFE/BLOCKED.
    """
    cmd = command.strip()

    for pattern, reason in _BLOCKED:
        if pattern.search(cmd):
            return CommandLevel.BLOCKED, reason, ""

    for pattern, reason, key in _ELEVATED:
        if pattern.search(cmd):
            # mv and redirect overwrite are SAFE when all targets are in /tmp/
            if key in ("mv", "redirect_overwrite") and _all_targets_tmp(cmd):
                continue
            return CommandLevel.ELEVATED, reason, key

    # Python/node scripts that don't exist yet are ELEVATED
    m = re.search(r"\bpython3?\s+([^\s;|&<>]+\.py)\b", cmd)
    if m:
        script = m.group(1)
        if not Path(script).expanduser().exists():
            return CommandLevel.ELEVATED, f"script {script!r} does not exist yet", f"script:{script}"

    return CommandLevel.SAFE, "", ""
