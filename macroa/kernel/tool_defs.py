"""Tool definitions exposed to the agent LLM — OpenAI schemas and executors."""

from __future__ import annotations

import html as _html_mod
import logging
import re
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
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using DuckDuckGo and return a list of results with titles, "
                "URLs, and snippets. Use this to research topics, find recent information, "
                "discover sources to cite, or look up anything not in memory. "
                "Follow up with fetch_url to read the full content of promising pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (be specific for best results)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ipc_emit",
            "description": (
                "Write a message to a named IPC channel. Any other agent listening on the "
                "same channel (via ipc_read) will receive it. Channels are in-memory only "
                "and do not persist across kernel restarts. Use this to coordinate with "
                "other agents running in parallel sessions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name (e.g. 'results', 'alerts', 'agent-b')",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message content to send",
                    },
                },
                "required": ["channel", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ipc_read",
            "description": (
                "Block-read the next message from a named IPC channel. Returns the message "
                "or a timeout notice if no message arrives within the timeout window. "
                "Use this to receive results or signals from another agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name to read from",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Seconds to wait for a message (default 5, max 60)",
                    },
                },
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ipc_list_channels",
            "description": (
                "List all active IPC channels and the number of pending (unread) messages "
                "in each. Use this to discover what channels exist and whether any have "
                "messages waiting."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch the text content of a web page (HTML stripped). "
                "Use after web_search to read full articles, documentation, or sources. "
                "Content is truncated at 8000 characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to fetch (must start with http:// or https://)",
                    },
                },
                "required": ["url"],
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
        if name == "web_search":
            return _web_search(args["query"], drivers)
        if name == "fetch_url":
            return _fetch_url(args["url"], drivers)
        if name == "ipc_emit":
            return _ipc_emit(args["channel"], args["message"], drivers)
        if name == "ipc_read":
            return _ipc_read(args["channel"], float(args.get("timeout", 5.0)), drivers)
        if name == "ipc_list_channels":
            return _ipc_list_channels(drivers)
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
    results = drivers.memory.search_fts(query, limit=10)
    if not results:
        return "No memories found."
    return "\n".join(
        f"- {r['key']}: {r['value']}" + (f" (confidence: {r['confidence']:.2f})" if r.get("confidence", 1.0) < 1.0 else "")
        for r in results
    )


def _web_search(query: str, drivers: DriverBundle) -> str:  # noqa: ARG001
    try:
        from ddgs import DDGS
    except ImportError:
        return "[web_search error: ddgs not installed — run: pip install ddgs]"

    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=8))
    except Exception as exc:
        logger.warning("web_search failed: %s", exc)
        return f"[web_search error: {exc}]"

    if not hits:
        return f"[No search results found for: {query!r}]"

    lines = [f"Web search results for: {query}\n"]
    for i, r in enumerate(hits):
        lines.append(f"{i + 1}. {r.get('title', '(no title)')}")
        lines.append(f"   URL: {r.get('href', '')}")
        body = r.get("body", "")
        if body:
            lines.append(f"   {body[:200]}")
        lines.append("")
    return "\n".join(lines)


def _ipc_emit(channel: str, message: str, drivers: DriverBundle) -> str:
    if drivers.ipc is None:
        return "[ipc_emit error: IPC bus not available]"
    drivers.ipc.emit(channel, message)
    return f"Message sent to channel {channel!r} ({len(message)} chars)"


def _ipc_read(channel: str, timeout: float, drivers: DriverBundle) -> str:
    if drivers.ipc is None:
        return "[ipc_read error: IPC bus not available]"
    timeout = min(timeout, 60.0)
    msg = drivers.ipc.read(channel, timeout=timeout)
    if msg is None:
        return f"[ipc_read: no message on {channel!r} after {timeout:.1f}s]"
    source = f" (from {msg['source']!r})" if msg.get("source") else ""
    return f"[channel={msg['channel']}{source}] {msg['content']}"


def _ipc_list_channels(drivers: DriverBundle) -> str:
    if drivers.ipc is None:
        return "[ipc_list_channels error: IPC bus not available]"
    channels = drivers.ipc.list_channels()
    if not channels:
        return "No active IPC channels."
    lines = ["Active IPC channels:"]
    for ch in channels:
        lines.append(f"  {ch['channel']}: {ch['pending']} pending")
    return "\n".join(lines)


def _fetch_url(url: str, drivers: DriverBundle) -> str:
    resp = drivers.network.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Macroa/1.0)"},
        timeout=20,
    )
    if not resp.success:
        return f"[fetch_url error: {resp.error}]"

    body = resp.body
    body = re.sub(r"<script[^>]*>.*?</script>", " ", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = _html_mod.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()

    if len(body) > 8000:
        body = body[:8000] + "\n[... page truncated at 8000 chars]"
    return body
