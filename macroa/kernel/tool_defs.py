"""Tool definitions exposed to the agent LLM — OpenAI schemas and executors."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from macroa.kernel.sudo import CommandLevel, classify
from macroa.stdlib.schema import DriverBundle

logger = logging.getLogger(__name__)

ConfirmCallback = Callable[[str, str], bool]

# ── OpenAI-format tool schemas ────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write text content to a file. Creates the file and any missing parent "
                "directories automatically. Use this to create ~/.macroa/IDENTITY.md, "
                "USER.md, SOUL.md, scripts, configs, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path or path starting with ~ (e.g. ~/.macroa/IDENTITY.md)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read and return the text contents of a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to read (absolute or starting with ~)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command on the local system. "
                "Safe commands run immediately. Elevated commands (rm, mv, pip install, "
                "git push, etc.) pause and ask the user for permission. "
                "Blocked commands (rm -rf /, disk format, remote code execution) are "
                "always rejected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store a persistent fact in the user's memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Fact identifier, e.g. 'user_name'"},
                    "value": {"type": "string", "description": "Fact value, e.g. 'Alice'"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search the user's persistent memory for stored facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
]


# ── Executor ──────────────────────────────────────────────────────────────────

def execute_tool(
    name: str,
    args: dict,
    drivers: DriverBundle,
    session_approved: set[str],
    confirm_callback: ConfirmCallback | None,
) -> str:
    """Dispatch a tool call by name. Returns a string to feed back to the LLM."""
    try:
        if name == "write_file":
            return _write_file(args["path"], args["content"], drivers)
        if name == "read_file":
            return _read_file(args["path"], drivers)
        if name == "run_command":
            return _run_command(args["command"], drivers, session_approved, confirm_callback)
        if name == "remember":
            return _remember(args["key"], args["value"], drivers)
        if name == "recall":
            return _recall(args["query"], drivers)
        return f"[unknown tool: {name!r}]"
    except KeyError as exc:
        return f"[tool {name!r} missing required argument: {exc}]"
    except Exception as exc:
        logger.error("Tool %r raised: %s", name, exc, exc_info=True)
        return f"[tool {name!r} error: {exc}]"


def _write_file(path: str, content: str, drivers: DriverBundle) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    written = drivers.fs.write(str(p), content)
    return f"Written {len(content)} chars to {written}"


def _read_file(path: str, drivers: DriverBundle) -> str:
    p = str(Path(path).expanduser())
    return drivers.fs.read(p)


def _run_command(
    command: str,
    drivers: DriverBundle,
    session_approved: set[str],
    confirm_callback: ConfirmCallback | None,
) -> str:
    level, reason, key = classify(command)

    if level == CommandLevel.BLOCKED:
        return f"[BLOCKED] {reason}"

    if level == CommandLevel.ELEVATED:
        if key not in session_approved:
            if confirm_callback is None or not confirm_callback(command, reason):
                return f"[DENIED] Requires elevated permission ({reason}). Approved: no."
            session_approved.add(key)

    exit_code, stdout, stderr = drivers.shell.run(command)
    parts: list[str] = []
    if stdout:
        parts.append(stdout.rstrip())
    if stderr:
        parts.append(f"[stderr] {stderr.rstrip()}")
    if not parts:
        parts.append(f"(exit {exit_code})")
    return "\n".join(parts)


def _remember(key: str, value: str, drivers: DriverBundle) -> str:
    drivers.memory.set("user", key, value)
    return f"Remembered: {key} = {value}"


def _recall(query: str, drivers: DriverBundle) -> str:
    results = drivers.memory.search(query)
    if not results:
        return "No memories found."
    return "\n".join(f"- {r['key']}: {r['value']}" for r in results)
