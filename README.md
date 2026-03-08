> **⚠️ ARCHIVED , This repository is legacy.** Development has pivoted to a new architecture.
>
> The original Macroa was a Python-based personal AI assistant. After a full design review, the project was redesigned from scratch as **Macroa: an AI agent operating system**, a platform that other agents run on, the way apps run on Windows.
>
> The original code is preserved here for reference. The new build starts from zero with a clear architecture designed before any code.
>
> **Active development:**
> - [Macroa-Pulse](https://github.com/aniolowie/Macroa-Pulse) , the proactive cognition subsystem, built first
> - Macroa kernel, SDK, and shell , coming after Pulse
>
> The core pivot: every AI agent framework today is reactive. The new Macroa is built around the **Pulse**, a system that enables agents to notice when something is worth attention and act on it, without cron jobs, webhooks, or LLM polling, and around one principle: *AI is used only where no deterministic process can do the job. Everything else is infrastructure.*

---

<div align="center">

```
╔══════════════════════════════════════════════╗
║                                              ║
║   ███╗   ███╗ █████╗  ██████╗██████╗  ██████╗  █████╗  ║
║   ████╗ ████║██╔══██╗██╔════╝██╔══██╗██╔═══██╗██╔══██╗ ║
║   ██╔████╔██║███████║██║     ██████╔╝██║   ██║███████║ ║
║   ██║╚██╔╝██║██╔══██║██║     ██╔══██╗██║   ██║██╔══██║ ║
║   ██║ ╚═╝ ██║██║  ██║╚██████╗██║  ██║╚██████╔╝██║  ██║ ║
║   ╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝ ║
║                                              ║
╚══════════════════════════════════════════════╝
```

### Your personal AI OS — running on your machine, answering to you.

> *Routes every request to the right model tier automatically. Remembers everything you tell it.*
> *Runs shell commands, manages files, and schedules tasks — no AI overhead when you don't need it.*
> *Streams token by token. Learns semantically. Runs on every channel. Orchestrates agents in parallel.*

<br>

[![CI](https://github.com/aniolowie/Macroa/actions/workflows/ci.yml/badge.svg)](https://github.com/aniolowie/Macroa/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/aniolowie/Macroa/graph/badge.svg)](https://codecov.io/gh/aniolowie/Macroa)
[![PyPI](https://img.shields.io/pypi/v/macroa?color=blue&logo=pypi&logoColor=white)](https://pypi.org/project/macroa/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?logo=opensourceinitiative&logoColor=white)](LICENSE)
[![Downloads](https://img.shields.io/pypi/dm/macroa?color=orange&logo=pypi&logoColor=white)](https://pypi.org/project/macroa/)
[![Stars](https://img.shields.io/github/stars/aniolowie/Macroa?style=flat&logo=github&color=yellow)](https://github.com/aniolowie/Macroa/stargazers)

<br>

| 🧠 Smart routing | 💾 Vector memory | ⚡ Zero-cost ops | 🌐 Every channel |
|:---:|:---:|:---:|:---:|
| Right model for every task | FTS5 + semantic embeddings | Shell & memory skip the LLM | REST · SSE · Telegram · Discord |

| 🤖 Multi-agent | 🔄 Real streaming | 👾 Daemon mode | 💸 Cost tracking |
|:---:|:---:|:---:|:---:|
| Parallel DAG execution | Token-by-token REPL + SSE | Always-on background process | Per-turn dollar amounts |

</div>

---

<div align="center">

[![Star History Chart](https://api.star-history.com/image?repos=aniolowie/Macroa&type=date&legend=top-left)](https://www.star-history.com/?repos=aniolowie%2FMacroa&type=date&legend=top-left)

</div>

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

Routing and memory — the majority of calls in normal use — cost nearly nothing. Opus is only invoked when you explicitly demand it. Every turn shows exact token counts and dollar spend in debug mode.

---

## Table of contents

- [Installation](#installation)
- [Setup](#setup)
- [Quick start](#quick-start)
- [REPL](#repl)
- [Single-shot mode](#single-shot-mode)
- [Sessions](#sessions)
- [Scheduler](#scheduler)
- [Daemon mode](#daemon-mode)
- [Channel adapters](#channel-adapters)
- [Webhooks](#webhooks)
- [Multi-agent orchestration](#multi-agent-orchestration)
- [Tools](#tools)
- [HTTP API & dashboard](#http-api--dashboard)
- [Architecture](#architecture)
- [Model tiers](#model-tiers)
- [Memory layers](#memory-layers)
- [Writing a tool](#writing-a-tool)
- [Project layout](#project-layout)
- [Configuration reference](#configuration-reference)
- [Tests](#tests)
- [Contributing](#contributing)

---

## Installation

**Requirements:** Python 3.11+, an [OpenRouter](https://openrouter.ai) API key.

### Quick install via pip *(recommended)*

```bash
pip install macroa
```

With the web API, dashboard, and channel adapters:

```bash
pip install "macroa[web]"       # REST API + SSE + dashboard
pip install "macroa[telegram]"  # Telegram bot adapter
pip install "macroa[discord]"   # Discord gateway adapter
pip install "macroa[all]"       # everything
```

### Install from source *(for development)*

```bash
git clone https://github.com/aniolowie/Macroa.git
cd Macroa
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,web]"
```

---

## Setup

Run the interactive wizard — it only fires once, or whenever you call it explicitly:

```bash
macroa setup
```

The wizard asks for your OpenRouter API key, display name, and model preferences, then writes everything to `~/.macroa/.env`. Settings survive pip reinstalls and work across projects.

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
→ (Sonnet-tier response, streamed token by token)

macroa> research quantum computing trends in 2025
→ (4-phase multi-agent pipeline: orchestrate → investigate × N → verify → synthesize)

macroa> remind me to review the PR in 30 minutes
→ Reminder set. I'll notify you at 14:32.
```

---

## REPL

```bash
macroa                    # ephemeral session
macroa --session work     # named session — persists across restarts
macroa --debug            # show model tier, token count, cost, and latency per response
```

Responses stream token by token as they're generated.

Built-in REPL commands:

| Command | Effect |
|---------|--------|
| `!<cmd>` | Run a shell command directly — no AI involved |
| `clear` | Wipe the current session's context |
| `debug` | Toggle debug metadata (tier, tokens, cost, latency) |
| `help` | Show quick-reference |
| `exit` / `quit` / `q` | Graceful shutdown |

**Debug output example:**

```
→ The CAP theorem states…
  [sonnet · 342tok · $0.00205 · 1.8s]
```

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

## Daemon mode

Run Macroa as a persistent background process. The daemon keeps the scheduler alive, serves the web API, and handles reminders even when no REPL is open.

```bash
macroa daemon start              # spawn background process
macroa daemon start --port 9000  # custom web port
macroa daemon start --no-web     # scheduler only, no HTTP server
macroa daemon status             # show PID, active tasks, port, uptime
macroa daemon stop               # graceful shutdown (SIGTERM + 5s wait)
```

The REPL banner shows live daemon state on startup:

```
daemon: running  tasks: 4  web: :8000  uptime: 2h14m
```

Status is written to `~/.macroa/daemon_status.json` every 30 seconds. The PID file at `~/.macroa/daemon.pid` is auto-cleaned on stale detection.

---

## Channel adapters

Connect Macroa to messaging platforms. Every message routes through the full kernel — routing, memory, agents, tools, and reminders all work identically.

### Telegram

```bash
macroa telegram --token BOT_TOKEN
# or via env: MACROA_TELEGRAM_TOKEN=...

macroa telegram --token BOT_TOKEN --allow 123456789 --allow 987654321
```

Built-in bot commands: `/start`, `/help`, `/clear`. Messages split automatically at 4096 characters. On invalid token, the adapter fails fast with a clear error (no retry loop).

### Discord

```bash
macroa discord --token BOT_TOKEN
macroa discord --token BOT_TOKEN --channel 123456 --allow USER_ID
```

Uses the Discord Gateway (WebSocket) when `websockets` is installed; falls back to REST polling otherwise. Built-in slash commands: `/macroa help`, `/macroa clear`. Messages split at 2000 characters. Auto-reconnects on gateway drop.

---

## Webhooks

Inbound HTTP webhooks fire `kernel.run()` like a cron job, but event-driven. Each webhook gets a unique secret key and a configurable message template.

```bash
# Create a webhook
curl -X POST http://localhost:8000/webhooks \
  -H "Content-Type: application/json" \
  -d '{"name": "github-push", "template": "new commit from {{pusher.name}}: {{head_commit.message}}"}'

# Trigger it (from GitHub, Zapier, or any HTTP client)
curl -X POST "http://localhost:8000/webhook/github-push?key=SECRET" \
  -d '{"pusher": {"name": "alice"}, "head_commit": {"message": "fix bug"}}'
```

Template placeholders: `{{body}}` (full JSON), `{{field}}`, `{{field.nested}}`. The rendered string is passed directly to `kernel.run()` and the response is returned as JSON.

**Webhook management:**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhooks` | Create a webhook, receive auto-generated secret |
| `GET` | `/webhooks` | List all registered webhooks |
| `DELETE` | `/webhooks/{name}` | Remove a webhook |
| `POST` | `/webhook/{name}?key=SECRET` | Trigger a webhook |

---

## Multi-agent orchestration

Run multiple named agents in parallel, with dependency ordering. Independent tasks execute concurrently; dependent tasks receive predecessor output as injected context.

```python
from macroa.kernel.multi_agent import AgentTask
from macroa.stdlib.schema import ModelTier

result = kernel.run_agents(
    tasks=[
        AgentTask(name="research",  objective="Research quantum computing trends"),
        AgentTask(name="analysis",  objective="Analyse the research findings", depends_on=["research"]),
        AgentTask(name="summary",   objective="Write an executive summary",    depends_on=["analysis"]),
        AgentTask(name="citations", objective="Format all citations",           depends_on=["research"],
                  model_tier=ModelTier.HAIKU),
    ],
    original_request="Produce a quantum computing briefing",
    session_id="my-session",
)
```

- Tasks with no dependencies run in the first wave — in parallel threads
- Each subsequent wave runs tasks whose dependencies are all complete
- Failed dependency → all dependents are marked failed automatically (no silent corruption)
- Results are merged by a HAIKU synthesizer into one coherent response
- Agents can spawn subagents using the `spawn_agent` tool from within a tool-calling loop
- 120-second safety cap per agent; 8 agents maximum per coordinator

---

## Tools

Tools are Python packages that extend Macroa with any capability — API integrations, services, automation:

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
| `GET` | `/run/stream` | Run a command, **true** token-streaming via SSE |
| `GET` | `/sessions` | List named sessions |
| `DELETE` | `/sessions/{name}` | Delete a session |
| `POST` | `/schedule` | Add a scheduled task |
| `GET` | `/schedule` | List scheduled tasks |
| `DELETE` | `/schedule/{id}` | Remove a scheduled task |
| `GET` | `/audit/stats` | Aggregate usage stats |
| `GET` | `/audit/recent` | Recent run log with token counts and cost |
| `POST` | `/webhooks` | Register a webhook |
| `GET` | `/webhooks` | List webhooks |
| `DELETE` | `/webhooks/{name}` | Delete a webhook |
| `POST` | `/webhook/{name}` | Trigger a webhook |
| `GET` | `/dashboard` | Web dashboard UI |
| `GET` | `/health` | Health check |

### Streaming example

```bash
# Token-by-token SSE stream
curl -N "http://localhost:8000/run/stream?input=tell+me+about+quantum+computing&session=work"

# data: The field of quantum
# data:  computing leverages…
```

### Standard request

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"input": "what is my server IP", "session": "work"}'
```

---

## Architecture

```
User input (REPL · single-shot · HTTP · Telegram · Discord · Webhook)
    │
    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ENTRY POINTS                                                       │
│  CLI (macroa / macroa run "…")                                      │
│  macroa serve → FastAPI (REST + true SSE streaming + Dashboard)     │
│  macroa telegram / discord → Channel adapters (per-user sessions)   │
│  POST /webhook/{name} → Webhook triggers                            │
│  macroa daemon → Always-on background process (PID + heartbeat)     │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ kernel.run()
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  KERNEL                                                             │
│  Router(NANO)          → hard-route (!cmd) or LLM classify          │
│  Planner(NANO)         → decompose complex tasks into steps         │
│  Dispatcher            → skill.run() with escalation loop           │
│  Combiner(HAIKU)       → assemble multi-step results                │
│  AgentLoop             → tool-calling loop (up to 20 rounds)        │
│  MultiAgentCoordinator → DAG wave execution, parallel threads       │
│  SessionStore          → named sessions, context persistence        │
│  Scheduler             → cron/once/every/daily background tasks     │
│  AuditLog              → every call recorded (tokens + cost)        │
│  EventBus              → pub/sub for tools, reminders, research     │
│  clock.now_context()   → live time/timezone injected into prompts   │
└──────────────┬─────────────────────────┬───────────────────────────┘
               │                         │
     ┌─────────┴──────────┐    ┌─────────┴────────────────────────┐
     ▼                    ▼    ▼                                   ▼
┌─────────┐    ┌──────────────────────┐   ┌──────────────────────────┐
│ SKILLS  │    │ TOOLS (userspace)    │   │ MEMORY PIPELINE          │
│ shell   │    │ ~/.macroa/tools/     │   │ Extractor → store facts  │
│ file    │    │ BaseTool: setup /    │   │ Retriever → FTS5 search  │
│ memory  │    │  execute / heartbeat │   │ SemanticRetriever        │
│ chat    │    │ HeartbeatManager     │   │  FTS5 + vector similarity │
│ agent   │    │ Installer: path/git  │   │ EmbeddingStore (SQLite)  │
│ research│    └──────────────────────┘   │ ContextCompactor         │
│ reminder│                               │  (episodic summaries)    │
└────┬────┘                               └──────────────────────────┘
     ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DRIVERS (hardware abstraction)                                     │
│  llm     → OpenRouter (NANO/HAIKU/SONNET/OPUS) + streaming          │
│  llm     → embed() → OpenRouter Embeddings API (text-embedding-3)   │
│  shell   → subprocess, stdout/stderr capture, permission tiers      │
│  fs      → filesystem, $HOME-scoped path validation                 │
│  memory  → SQLite v2 (facts + episodes + FTS5) or JSON              │
│  network → HTTP client (stdlib, zero extra deps)                    │
└─────────────────────────────────────────────────────────────────────┘
```

**Core design rule:** deterministic operations never call an LLM. AI is reserved for ambiguity, reasoning, and generation.

### Request lifecycle

1. **Router** (always NANO) — classifies input to a skill. Shell commands (`!`) hard-route and skip the LLM entirely.
2. **Planner** (NANO, optional) — checks if the request is complex. Short inputs skip the planner. For complex tasks, decomposes into 2–5 steps and assigns each the cheapest tier that can handle it.
3. **Dispatcher** — runs the skill. If it returns `needs_reasoning=True`, promotes to the next tier and retries (up to 2 escalations).
4. **Combiner** (HAIKU) — for multi-step plans, assembles all step outputs into one coherent response.
5. **AuditLog** — every call recorded automatically to `~/.macroa/audit.db` with token counts and dollar cost.
6. **SessionStore** — context serialised to SQLite after every turn; compacted episodes injected into subsequent prompts.

---

## Model tiers

| Tier | Hardware analogy | Default model | Role |
|------|-----------------|---------------|------|
| `NANO` | Microcontroller | `google/gemini-2.5-flash-lite` | All routing, planning, trivial ops |
| `HAIKU` | Efficiency cores | `google/gemini-2.5-flash-lite` | Lightweight tasks, combining, reminders |
| `SONNET` | Performance cores | `anthropic/claude-sonnet-4-6` | Quality writing, reasoning, analysis |
| `OPUS` | GPU | `anthropic/claude-opus-4-6` | Maximum reasoning (use sparingly) |

**Forcing a tier from your prompt:**

```
macroa> use haiku to summarise this paragraph
macroa> think carefully and use sonnet to review this code
macroa> use the best model to analyse this architecture
```

**Automatic escalation:** if a skill signals `needs_reasoning=True`, the dispatcher promotes to the next tier and retries. Happens automatically, up to twice per request.

---

## Memory layers

| Layer | Storage | Contents | Retrieval |
|-------|---------|----------|-----------|
| Working | RAM (`ContextManager`) | Current conversation turns | Automatic — injected into every prompt |
| Semantic | SQLite `facts` | User facts, preferences — confidence-scored, expirable | Exact key (free) · FTS5 keyword · vector similarity |
| Episodic | SQLite `episodes` | Session summaries, compacted context | FTS5 search |
| Embeddings | SQLite `embeddings.db` | float32 vectors for every stored fact | Cosine similarity (pure Python) |
| Session | SQLite `sessions.db` | Named sessions + full context | Restored on session resume |

### Vector memory

Every fact written via `memory.set()` is automatically queued for background embedding via the OpenRouter Embeddings API (`text-embedding-3-small`, 1536 dimensions). Embeddings are stored as packed float32 blobs — no numpy required.

Retrieval merges two buckets:
- **FTS5** — BM25-ranked keyword matches (fast, offline)
- **Semantic** — cosine similarity against query embedding (understands synonyms and paraphrase)

Duplicate keys are deduplicated; semantic results that FTS5 missed are appended. The combined list is re-ranked by confidence before injection into the system prompt.

### Context compaction

When the rolling context window evicts an entry, the `ContextCompactor` summarises it into a 1–2 sentence episodic memory using the NANO model (in a daemon thread — never blocks the main turn). Compacted episodes are injected back into subsequent `chat_skill` and `agent_skill` prompts under a "Earlier in this conversation" section, so long conversations never lose important context.

---

## Writing a tool

Drop a directory into `~/.macroa/tools/` or use `macroa install`, and it's picked up automatically on the next run:

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
| `drivers.llm` | `complete()`, `stream()`, `embed()` — call any model tier |
| `drivers.shell` | `run(command)` → `(exit_code, stdout, stderr)` |
| `drivers.fs` | `read()`, `write()`, `list_dir()`, `exists()` — $HOME-scoped |
| `drivers.memory` | `set()`, `get()`, `search_fts()`, `list_all()`, `add_episode()` |
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
│   ├── llm_driver.py      # OpenRouter (blocking + streaming + embeddings)
│   ├── shell_driver.py    # subprocess, ANSI strip, 50k output cap
│   ├── fs_driver.py       # filesystem, $HOME-scoped path validation
│   ├── memory_driver.py   # SQLite v2 (facts + episodes + FTS5); embedding hook
│   └── network_driver.py  # HTTP client (stdlib only, zero extra deps)
├── skills/                # Kernel built-ins
│   ├── shell_skill.py     # Runs subprocess — never calls LLM
│   ├── file_skill.py      # Read/write/list via FSDriver
│   ├── memory_skill.py    # Set/get/search facts — exact reads skip LLM
│   ├── chat_skill.py      # LLM conversation — streams, injects memory + time
│   ├── agent_skill.py     # Tool-calling loop (up to 20 rounds)
│   ├── research_skill.py  # 4-phase multi-agent web research pipeline
│   └── reminder_skill.py  # Add/list/delete reminders; timezone-aware
├── kernel/
│   ├── router.py          # Intent classification (NANO)
│   ├── planner.py         # Task decomposition (NANO) + combiner (HAIKU)
│   ├── dispatcher.py      # Escalation loop
│   ├── escalation.py      # Tier resolution and promotion logic
│   ├── context.py         # Rolling context window + on_evict hook
│   ├── sessions.py        # Named sessions + context persistence
│   ├── scheduler.py       # Cron/once/every/daily background task runner
│   ├── events.py          # Thread-safe pub/sub event bus
│   ├── audit.py           # Immutable run log (tokens + cost per turn)
│   ├── agent.py           # AgentLoop — context-aware tool-calling
│   ├── tool_defs.py       # Built-in agent tools + spawn_agent
│   ├── multi_agent.py     # MultiAgentCoordinator — DAG wave execution
│   ├── clock.py           # now_context() — live time injected into prompts
│   ├── daemon.py          # Daemon process lifecycle (PID + status JSON)
│   └── __init__.py        # Public API: kernel.run(), run_agents(), shutdown()…
├── memory/
│   ├── semantic.py        # EmbeddingStore (SQLite float32) + SemanticRetriever
│   ├── compactor.py       # ContextCompactor — episodic summaries on eviction
│   ├── extractor.py       # MemoryExtractor — LLM-powered fact extraction
│   ├── retriever.py       # retrieve() — unified FTS5 + semantic fetch
│   └── formatter.py       # format_for_prompt() — memory → prompt section
├── channels/
│   ├── base.py            # BaseAdapter ABC — per-user sessions, thread lifecycle
│   ├── telegram.py        # TelegramAdapter — long-polling, 4096-char splitting
│   └── discord.py         # DiscordAdapter — gateway WebSocket + REST fallback
├── web/
│   ├── app.py             # FastAPI app (REST + real SSE streaming + webhooks)
│   ├── webhooks.py        # WebhookConfig + WebhookStore (SQLite-backed)
│   └── static/
│       └── dashboard.html # Web dashboard
└── cli/
    ├── wizard.py          # First-run setup wizard
    ├── renderer.py        # Rich terminal output + daemon status banner
    └── main.py            # Click CLI + REPL + daemon/telegram/discord commands
```

---

## Configuration reference

All settings are environment variables. The wizard writes them to `~/.macroa/.env`. Project-level `.env` in the CWD takes priority over wizard defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | **Required.** Your OpenRouter key. |
| `MACROA_USER_NAME` | — | Display name shown in the startup banner. |
| `MACROA_MODEL_NANO` | `google/gemini-2.5-flash-lite` | Routing and trivial ops tier. |
| `MACROA_MODEL_HAIKU` | `google/gemini-2.5-flash-lite` | Efficiency-cores tier. |
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
| `MACROA_AUDIT_DB_PATH` | `~/.macroa/audit.db` | Audit log (tokens + cost per turn). |
| `MACROA_NETWORK_TIMEOUT` | `30` | Default HTTP timeout (seconds) for tools. |
| `MACROA_TELEGRAM_TOKEN` | — | Telegram bot token (alternative to `--token` flag). |
| `MACROA_DISCORD_TOKEN` | — | Discord bot token (alternative to `--token` flag). |

---

## Tests

```bash
pytest tests/ -v
pytest tests/ --cov=macroa --cov-report=term-missing
```

270+ tests across: router, skills, drivers, memory, semantic/vector memory, scheduler, sessions, audit, research pipeline, tool installer, sudo classifier, identity, web API, webhooks, multi-agent, daemon, channel adapters.

Zero external dependencies required — all LLM and embedding calls are mocked. The test suite runs fully offline.

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
