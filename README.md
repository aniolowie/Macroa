<div align="center">

# Macroa

**Your personal AI OS — running on your machine, answering to you.**

[![CI](https://github.com/aniolowie/Macroa/actions/workflows/ci.yml/badge.svg)](https://github.com/aniolowie/Macroa/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/aniolowie/Macroa/graph/badge.svg)](https://codecov.io/gh/aniolowie/Macroa)
[![PyPI](https://img.shields.io/pypi/v/macroa)](https://pypi.org/project/macroa/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/macroa)](https://pypi.org/project/macroa/)

Routes every request to the right model tier automatically. Remembers everything you tell it. Runs shell commands, manages files, and schedules tasks — no AI overhead for operations that don't need it. Exposes a full HTTP API and web dashboard. Installs in under a minute.

</div>

---

[![Star History Chart](https://api.star-history.com/image?repos=aniolowie/Macroa&type=date&legend=top-left)](https://www.star-history.com/?repos=aniolowie%2FMacroa&type=date&legend=top-left)

---

## What makes it different

Most AI assistants either call an expensive model for everything, or leave you to manage prompts and routing yourself. Macroa treats AI like a CPU: cheap cores handle simple work, powerful cores handle complex work, and purely deterministic operations never touch a model at all.

| Operation | What runs | Cost |
|-----------|-----------|------|
| `!df -h` | subprocess | **$0** |
| `remember my server IP is 10.0.0.1` | SQLite write | **$0** |
| `what is my server IP?` | SQLite read | **$0** |
| Routing (what do you want?) | Gemini Flash Lite | ~$0.0001 |
| Simple chat / summarisation | Haiku | low |
| `think carefully about…` | Sonnet | medium |
| `use the best model…` | Opus | higher |

The result: routing and memory — the majority of calls in normal use — cost nearly nothing. Opus is only invoked when you explicitly demand it.

---

## Table of contents

- [Installation](#installation)
- [Setup](#setup)
- [Quick start](#quick-start)
- [REPL](#repl)
- [Single-shot mode](#single-shot-mode)
- [Sessions](#sessions)
- [Scheduler](#scheduler)
- [Tools](#tools)
- [HTTP API & dashboard](#http-api--dashboard)
- [Architecture](#architecture)
- [Model tiers](#model-tiers)
- [Memory layers](#memory-layers)
- [Writing a tool](#writing-a-tool)
- [Configuration reference](#configuration-reference)
- [Tests](#tests)

---

## Installation

**Requirements:** Python 3.11+, an [OpenRouter](https://openrouter.ai) API key.

```bash
git clone https://github.com/aniolowie/Macroa.git
cd Macroa
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

For the web API and dashboard:

```bash
pip install -e ".[web]"
```

---

## Setup

Run the interactive wizard — it only fires once, or whenever you call it explicitly:

```bash
macroa setup
```

The wizard asks for your OpenRouter API key, display name, and model preferences, then writes everything to `~/.macroa/.env`. Settings survive pip reinstalls and work across projects.

Alternatively, copy the example env file and fill it in manually:

```bash
cp .env.example .env
# Add: OPENROUTER_API_KEY=sk-or-v1-...
```

That's it. Run `macroa` to start.

---

## Quick start

```
macroa> what is the capital of France
→ Paris.

macroa> remember my server IP is 10.0.0.1
→ Saved.

macroa> what is my server IP?
→ 10.0.0.1                       ← answered from SQLite, no API call

macroa> !df -h
→ (disk usage output)             ← subprocess, zero AI cost

macroa> think carefully about the tradeoffs of microservices
→ (Sonnet-tier response)

macroa> write a full guide on home lab security, networking, and monitoring
→ (Planner decomposes into steps → each runs at the right tier → combined)
```

---

## REPL

```bash
macroa                    # ephemeral session
macroa --session work     # named session — persists across restarts
macroa --debug            # show model tier, latency, and metadata per response
```

Built-in REPL commands:

| Command | Effect |
|---------|--------|
| `!<cmd>` | Run a shell command directly — no AI involved |
| `clear` | Wipe the current session's context |
| `debug` | Toggle debug metadata on/off |
| `help` | Show quick-reference |
| `exit` / `quit` / `q` | Graceful shutdown |

---

## Single-shot mode

Run one command and exit — useful for scripts and pipelines:

```bash
macroa run "!uname -a"
macroa run "remember my timezone is UTC+2"
macroa run "think carefully about the CAP theorem"
macroa run --session work "what did we discuss yesterday?"
macroa run --debug "summarise my last three sessions"
```

Exit code is `0` on success, `1` on failure — pipeable.

---

## Sessions

Named sessions persist to disk and survive process restarts. Context is serialised after every turn and restored on resume.

```bash
macroa --session work           # start or resume a session named "work"
macroa --session personal       # separate context, separate memory namespace

macroa sessions list            # show all named sessions with turn counts
macroa sessions delete work     # remove a session and its persisted context
```

Without `--session`, the session is ephemeral and discarded on exit.

---

## Scheduler

Commands run automatically in the background. The scheduler survives restarts and supports four recurrence formats.

```bash
# Add tasks
macroa schedule add "morning-brief"  "summarise my tasks for today"  "daily:08:00"
macroa schedule add "cleanup"        "!rm -rf /tmp/scratch"          "every:3600"
macroa schedule add "one-off"        "remind me to call John"        "once:1741600000"
macroa schedule add "weekly-report"  "write a summary of this week"  "cron:0 9 * * 1"

# Manage
macroa schedule list
macroa schedule list --all          # include disabled tasks
macroa schedule delete <id-prefix>
```

**Recurrence formats:**

| Format | Example | Meaning |
|--------|---------|---------|
| `once:<epoch>` | `once:1741600000` | Run once at this Unix timestamp |
| `every:<seconds>` | `every:3600` | Repeat every N seconds |
| `daily:<HH:MM>` | `daily:09:00` | Every day at this local time |
| `cron:<expression>` | `cron:0 9 * * 1` | Standard 5-field cron |

---

## Tools

Tools are Python packages that extend Macroa with any capability — API integrations, services, automation. Install from a local directory or a git URL:

```bash
macroa install /path/to/my_tool            # from local directory
macroa install https://github.com/user/repo            # from git
macroa install https://github.com/user/repo#subdir/tool  # git with subdir

macroa tools list                          # show installed tools
macroa uninstall my_tool                   # remove a tool
```

Once installed, just describe what you want — the kernel routes to the right tool automatically.

---

## HTTP API & dashboard

```bash
macroa serve                              # starts at http://localhost:8000
macroa serve --host 0.0.0.0 --port 9000  # bind to all interfaces
macroa serve --reload                     # dev mode with auto-reload
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/run` | Run a command, get full response |
| `GET` | `/run/stream` | Run a command, stream response via SSE |
| `GET` | `/sessions` | List named sessions |
| `DELETE` | `/sessions/{name}` | Delete a session |
| `POST` | `/schedule` | Add a scheduled task |
| `GET` | `/schedule` | List scheduled tasks |
| `DELETE` | `/schedule/{id}` | Remove a scheduled task |
| `GET` | `/audit/stats` | Aggregate usage stats |
| `GET` | `/audit/recent` | Recent run log |
| `GET` | `/dashboard` | Web dashboard UI |
| `GET` | `/health` | Health check |

### Example request

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"input": "what is my server IP", "session": "work"}'
```

### Dashboard

Open `http://localhost:8000/dashboard` after starting the server:

- Total runs, failure rate, plan calls
- Model tier distribution (cheap vs expensive over time)
- Active sessions and their turn counts
- Scheduled task queue with next-run times
- Audit log of recent activity

---

## Architecture

```
User input
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  CLI  (macroa / macroa run "...")                   │
│  macroa serve → FastAPI (HTTP + SSE + Dashboard)    │
└───────────────────────────┬─────────────────────────┘
                            │ kernel.run()
                            ▼
┌─────────────────────────────────────────────────────┐
│  KERNEL                                             │
│  Router(NANO) → hard-route (!cmd) or LLM classify  │
│  Planner(NANO) → decompose complex tasks            │
│  Dispatcher → skill.run() with escalation loop     │
│  Combiner(HAIKU) → assemble multi-step results      │
│  SessionStore → named sessions, context persistence │
│  Scheduler → cron/once/every/daily background tasks │
│  AuditLog → every call recorded automatically       │
│  EventBus → pub/sub for tools and heartbeat         │
└──────────────┬──────────────────────────────────────┘
               │
     ┌─────────┴──────────┐
     ▼                    ▼
┌─────────┐     ┌──────────────────────────────────┐
│ SKILLS  │     │ TOOLS (userspace programs)       │
│ shell   │     │ ~/.macroa/tools/<name>/tool.py   │
│ file    │     │ BaseTool: setup/execute/heartbeat │
│ memory  │     │ HeartbeatManager (persistent)    │
│ chat    │     │ Installer: local path or git URL  │
└────┬────┘     └──────────────────────────────────┘
     ▼
┌─────────────────────────────────────────────────────┐
│  DRIVERS (hardware abstraction)                     │
│  llm     → OpenRouter (NANO/HAIKU/SONNET/OPUS)     │
│  llm     → streaming variant (SSE)                 │
│  shell   → subprocess, stdout/stderr capture        │
│  fs      → filesystem, $HOME-scoped                 │
│  memory  → SQLite (facts + episodes) or JSON        │
│  network → HTTP client (stdlib, no extra deps)      │
└─────────────────────────────────────────────────────┘
```

**Core design rule:** deterministic operations never call an LLM. AI is reserved for ambiguity, reasoning, and generation.

### Request lifecycle

1. **Router** (always NANO) — classifies input to a skill + parameters. Shell commands (`!`) hard-route and skip the LLM entirely.
2. **Planner** (NANO, optional) — checks if the request is complex. Requests under 80 characters are trivially atomic and skip the planner. For complex tasks, the planner decomposes into 2–5 steps and assigns each step the cheapest tier that can handle it.
3. **Dispatcher** — runs the skill. If it returns `needs_reasoning=True`, promotes to the next tier and retries (up to 2 escalations).
4. **Combiner** (HAIKU) — for multi-step plans, assembles all step outputs into one coherent response.
5. **AuditLog** — every call is recorded automatically to `~/.macroa/audit.db`.
6. **SessionStore** — context serialised to SQLite after every turn.

---

## Model tiers

Macroa uses a hardware analogy to describe its model stack. Each tier maps to a class of work:

| Tier | Hardware analogy | Default model | Role |
|------|-----------------|---------------|------|
| `NANO` | Microcontroller | `google/gemini-2.5-flash-lite` | All routing, planning, trivial ops |
| `HAIKU` | Efficiency cores | `anthropic/claude-haiku-4-5` | Lightweight tasks, combining steps |
| `SONNET` | Performance cores | `anthropic/claude-sonnet-4-6` | Quality writing, reasoning, analysis |
| `OPUS` | GPU | `anthropic/claude-opus-4-6` | Maximum reasoning (use sparingly) |

**Forcing a tier from your prompt:**

```
macroa> use haiku to summarise this paragraph
macroa> think carefully and use sonnet to review this code
macroa> use the best model to analyse this architecture
```

Tier keywords are detected before routing — if present, they override everything else.

**Automatic escalation:** if a skill determines it needs more reasoning than the assigned tier can provide, it signals `needs_reasoning=True` and the dispatcher promotes to the next tier and retries. This happens automatically, up to twice per request.

---

## Memory layers

Macroa stores memory at three levels. Each layer has a different scope, lifetime, and retrieval method.

| Layer | Storage | Contents | Retrieval |
|-------|---------|----------|-----------|
| Working | RAM (`ContextManager` deque) | Current conversation turns | Automatic (injected into every prompt) |
| Semantic | SQLite `facts` table | User facts, preferences — confidence-scored, expirable | Exact key (free) or substring search |
| Episodic | SQLite `episodes` table | Session summaries, searchable by topic | Substring search |
| Session | SQLite `sessions.db` | Named sessions + full context (survives restarts) | Restored on session resume |

**Why SQLite over Markdown files:**
- Query power: `WHERE confidence > 0.8 AND expires_at IS NULL`
- ACID transactions — partial writes never corrupt state
- Indexed search: O(log n) vs O(n) full-file scan
- Zero server, zero cost — SQLite ships with every Python install

Exact-key memory reads never touch the LLM. `what is my server IP?` → SQL lookup → answer. No API call, no latency, no cost.

---

## Writing a tool

Drop a directory into `~/.macroa/tools/` — or use `macroa install` — and it's picked up automatically on the next run:

```
~/.macroa/tools/
  my_tool/
    tool.py      ← required
    .env         ← optional: MY_API_KEY=... (loaded automatically)
    helpers.py   ← optional: any other files
```

**`tool.py` template:**

```python
from macroa.tools.base import BaseTool, ToolManifest
from macroa.stdlib.schema import Context, DriverBundle, Intent, SkillResult

MANIFEST = ToolManifest(
    name="my_tool",
    description="Does X when the user asks for Y.",
    triggers=["do X", "run Y", "activate my thing"],
    persistent=False,   # True = heartbeat() fires every 60s
    timeout=30,
)

class MyTool(BaseTool):
    def setup(self, drivers: DriverBundle) -> None:
        """Called once on load — initialise connections, load config."""
        pass

    def execute(self, intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
        """Handle the request. Never raise — always return SkillResult."""
        return SkillResult(output="done", success=True, turn_id=intent.turn_id)

    def heartbeat(self, drivers: DriverBundle) -> None:
        """Fires every 60s if persistent=True — poll APIs, send alerts, etc."""
        pass

    def teardown(self, drivers: DriverBundle) -> None:
        """Called on shutdown — close connections, flush buffers."""
        pass
```

**Drivers available inside your tool:**

| Driver | What it gives you |
|--------|-------------------|
| `drivers.llm` | `complete()` and `stream()` — call any model tier |
| `drivers.shell` | `run(command)` → `(exit_code, stdout, stderr)` |
| `drivers.fs` | `read()`, `write()`, `list_dir()`, `exists()` — $HOME-scoped |
| `drivers.memory` | `set()`, `get()`, `search()`, `list_all()`, `add_episode()` |
| `drivers.network` | `get()`, `post()` — stdlib HTTP, no extra deps |

See `macroa/tools/examples/call_me/` for a complete reference implementation (Twilio phone call).

---

## Project layout

```
macroa/
├── stdlib/
│   ├── schema.py          # All shared dataclasses (Intent, SkillResult, ModelTier…)
│   └── text.py            # Deterministic string utilities (strip_ansi, truncate…)
├── config/
│   ├── settings.py        # Env/settings singleton (lru_cache)
│   └── skill_registry.py  # Auto-discovery of skills and tools
├── drivers/
│   ├── llm_driver.py      # OpenRouter via openai SDK (blocking + streaming)
│   ├── shell_driver.py    # subprocess, ANSI strip, 50k output cap
│   ├── fs_driver.py       # filesystem, $HOME-scoped path validation
│   ├── memory_driver.py   # SQLite v2 (facts + episodes) or JSON
│   └── network_driver.py  # HTTP client (stdlib only, zero extra deps)
├── skills/                # Kernel built-ins
│   ├── shell_skill.py     # Runs subprocess — never calls LLM
│   ├── file_skill.py      # Read/write/list via FSDriver
│   ├── memory_skill.py    # Set/get/search facts — exact reads skip LLM
│   └── chat_skill.py      # LLM fallback for everything else
├── kernel/
│   ├── router.py          # Intent classification (NANO)
│   ├── planner.py         # Task decomposition (NANO) + combiner (HAIKU)
│   ├── dispatcher.py      # Escalation loop
│   ├── escalation.py      # Tier resolution and promotion logic
│   ├── context.py         # Rolling context window (deque)
│   ├── sessions.py        # Named sessions + context persistence
│   ├── scheduler.py       # Cron/once/every/daily background task runner
│   ├── events.py          # Thread-safe pub/sub event bus
│   ├── audit.py           # Immutable run log (audit.db)
│   └── __init__.py        # Public API: kernel.run(), kernel.shutdown()…
├── tools/
│   ├── base.py            # BaseTool + ToolManifest
│   ├── registry.py        # Tool discovery and loading
│   ├── runner.py          # Timeout + error isolation
│   ├── installer.py       # Package manager (local + git)
│   ├── heartbeat.py       # Persistent tool daemon (HeartbeatManager)
│   └── examples/
│       └── call_me/       # Reference implementation (Twilio)
├── web/
│   ├── app.py             # FastAPI app (REST + SSE)
│   └── static/
│       └── dashboard.html # Web dashboard
└── cli/
    ├── wizard.py          # First-run setup wizard
    ├── renderer.py        # Rich terminal output
    └── main.py            # Click CLI + REPL
```

---

## Configuration reference

All settings are environment variables. The wizard writes them to `~/.macroa/.env`. You can also set them in a project-level `.env` file — project settings take priority over wizard defaults, which take priority over built-in defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | **Required.** Your OpenRouter key. |
| `MACROA_USER_NAME` | — | Display name shown in the startup banner. |
| `MACROA_MODEL_NANO` | `google/gemini-2.5-flash-lite` | Routing and trivial ops tier. |
| `MACROA_MODEL_HAIKU` | `anthropic/claude-haiku-4-5` | Efficiency-cores tier. |
| `MACROA_MODEL_SONNET` | `anthropic/claude-sonnet-4-6` | Performance-cores tier. |
| `MACROA_MODEL_OPUS` | `anthropic/claude-opus-4-6` | GPU tier (use sparingly). |
| `MACROA_CONTEXT_WINDOW` | `20` | Rolling turns kept in working memory. |
| `MACROA_MEMORY_BACKEND` | `sqlite` | `sqlite` or `json`. |
| `MACROA_MEMORY_DB_PATH` | `~/.macroa/memory.db` | Semantic + episodic memory store. |
| `MACROA_TOOLS_DIR` | `~/.macroa/tools` | User-installed tools directory. |
| `MACROA_HEARTBEAT_INTERVAL` | `60` | Seconds between heartbeat ticks for persistent tools. |
| `MACROA_SESSIONS_DB_PATH` | `~/.macroa/sessions.db` | Named session store. |
| `MACROA_SCHEDULER_DB_PATH` | `~/.macroa/scheduler.db` | Scheduled task store. |
| `MACROA_SCHEDULER_POLL` | `10` | Seconds between scheduler ticks. |
| `MACROA_AUDIT_DB_PATH` | `~/.macroa/audit.db` | Audit log (separate DB — not wiped with memory). |
| `MACROA_NETWORK_TIMEOUT` | `30` | Default HTTP timeout (seconds) for tools. |

---

## Tests

```bash
pytest tests/ -v
pytest tests/ --cov=macroa --cov-report=term-missing
```

170 tests. Zero external dependencies required — all LLM calls are mocked. The test suite runs offline.

---

## Roadmap

Phase 3 (planned):

- **Vector memory** — semantic search via embeddings, replacing substring match with BM25 + vector similarity
- **Agentic tool loop** — multi-turn tool use so the model can call tools, see results, and call more without chaining prompts manually
- **Context compaction** — auto-summarise evicted turns into episodic memory before they're dropped
- **Daemon mode** — always-on background process so the scheduler and heartbeat are persistent by default
- **Channel adapters** — Telegram and Discord bridges that route messages through the full kernel
- **Streaming REPL** — output appears token by token in the terminal
- **Multi-agent** — named agents with isolated workspaces, personas, and memory namespaces
- **Webhook triggers** — inbound HTTP webhooks fire `kernel.run()` like a cron job but event-driven
- **Cost tracking** — dollar amounts per session in the dashboard

---

## Contributing

Issues and PRs are welcome. See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for guidelines and [SECURITY.md](.github/SECURITY.md) for the responsible disclosure policy.

```bash
# dev setup
pip install -e ".[dev]"
ruff check macroa/
mypy macroa/
pytest tests/
```

---

<div align="center">

MIT License · Built by [aniolowie](https://github.com/aniolowie) · [Report a bug](https://github.com/aniolowie/Macroa/issues)

</div>
