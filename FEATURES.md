# Macroa Feature Reference — v0.2.9

## Core Pipeline

**Router** — Classifies every input into a skill + parameters using an LLM call (HAIKU/NANO). Has a keyword shortcut fast path for unambiguous triggers (shell, file, research, etc.) that bypasses the LLM. Memory skill is excluded from keyword shortcuts so parameter extraction (key/value/action) always goes through the LLM.

**Dispatcher** — Receives the routed intent, runs the appropriate skill, handles escalation if the skill signals it needs a stronger model.

**Context / Sessions** — Per-session message history stored in SQLite (`~/.macroa/sessions.db`). Named sessions persist across restarts (`macroa --session work`). Anonymous sessions are ephemeral.

---

## Skills

| Skill | What it does | Notes |
|---|---|---|
| `chat_skill` | General LLM conversation fallback | Injects identity + relevant memory into system prompt |
| `agent_skill` | Multi-step tool-calling loop (write files, run commands, use web, etc.) | Up to 20 rounds; requires user confirmation for elevated commands |
| `memory_skill` | Store/retrieve/search/delete/list persistent facts | SQLite-backed, namespaced |
| `shell_skill` | Run shell commands directly | Hard-routed via `!` or `$` prefix |
| `file_skill` | Read/write/list/exists for local files | |
| `research_skill` | 4-phase multi-agent web research → cited markdown report | Saves to `~/.macroa/research/` |

---

## Agent Tools (available inside `agent_skill`)

- `write_file` — create/overwrite any file
- `read_file` — read a file
- `run_command` — shell with tiered permission (safe/elevated/blocked)
- `remember` / `recall` — memory read/write
- `web_search` — DuckDuckGo via `ddgs` library, returns titles/URLs/snippets
- `fetch_url` — fetch and strip a web page to plain text (8000 char limit)

---

## Research Pipeline (research_skill)

Four phases, all mocked-out in tests:
1. **Plan** (SONNET) — decomposes query into 3–5 trajectories
2. **Investigate** (HAIKU x N) — each subagent runs OODA web search loop; forced summarize if max rounds hit without `<findings>`
3. **Verify** (HAIKU) — flags low-confidence claims across all findings
4. **Synthesize** (SONNET) — produces cited markdown report

**Live feed** — CLI prints real-time progress (phases, subagent starts, tool calls, source counts) via EventBus pub/sub.

---

## Memory

- SQLite backend (`~/.macroa/memory.db`)
- `facts` table: namespace + key/value + confidence + expiry
- `episodes` table: timestamped event log
- Full-text search across facts
- Schema versioning / migrations

---

## Identity Layer

Three files loaded from `~/.macroa/` on every boot:
- `IDENTITY.md` — agent name, nature, vibe, emoji
- `USER.md` — user name, timezone, preferences
- `SOUL.md` — values, behaviour limits

First boot (no `IDENTITY.md`): bootstrap mode — agent introduces itself and writes the files via `write_file`. Injected into `chat_skill` and `agent_skill` system prompts.

---

## Scheduler

Cron-equivalent daemon thread. Specs: `once:<ts>`, `every:<secs>`, `daily:<HH:MM>`, `cron:<5-field>`. CLI: `macroa schedule add/list/delete`. Runs commands via `kernel.run()` so audit log captures everything.

---

## Audit Log

Every `kernel.run()` call recorded to `~/.macroa/audit.db` — input, skill used, success/failure, timestamp. Not wiped when memory is cleared.

---

## Tool System

- User-installable tools from local path or git URL (`macroa install <path|url>`)
- Tools live in `~/.macroa/tools/`, auto-loaded at startup
- `BaseTool` base class with `setup()` + `execute()` + `heartbeat()`
- Optional persistent/background tools with heartbeat ticks (configurable interval)
- Example tool: `call_me` (Twilio phone call — requires env vars)
- CLI: `macroa install/uninstall/tools list`

---

## CLI

- `macroa` — interactive REPL
- `macroa run "<input>"` — single-shot
- `macroa --session <name>` — named session
- `macroa setup` — re-run wizard
- `macroa serve` — HTTP API server (requires `pip install macroa[web]`)
- `macroa sessions list/delete`
- `macroa schedule add/list/delete`
- `macroa tools list / install / uninstall`
- Built-in REPL commands: `help`, `clear`, `debug`, `exit`

---

## HTTP API (`macroa serve`)

FastAPI + SSE streaming. Endpoints: `POST /run`, `GET /stream`, `GET /health`. Optional dep (`pip install macroa[web]`). Static file serving built in.

---

## Setup Wizard

Runs on first boot if no API key found. Collects: OpenRouter key, user name, model preferences per tier. Writes to `~/.macroa/.env`. Re-runnable via `macroa setup`.

---

## Config / Settings

All settings from env vars or `.env` files. Priority: shell env → project `.env` (CWD only) → `~/.macroa/.env`. Four model tiers: NANO, HAIKU, SONNET, OPUS — all individually configurable. Strips accidental `openrouter/` prefix from model IDs.

---

## Tests

227 tests across: router, skills, drivers, memory, scheduler, sessions, audit, research pipeline, tool installer, sudo classifier, identity, web API.
