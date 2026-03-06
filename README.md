# Macroa

Your personal AI operating system. It runs on your machine, remembers everything you tell it, schedules tasks, routes to the right AI model automatically, and exposes a full HTTP API — all from a single command.

**Core design rule:** deterministic operations never call an LLM. AI is reserved for ambiguity, reasoning, and generation — keeping costs low and behavior predictable.



## Star History

[![Star History Chart](https://api.star-history.com/image?repos=aniolowie/Macroa&type=date&legend=top-left)](https://www.star-history.com/?repos=aniolowie%2FMacroa&type=date&legend=top-left)

---

## What it does for you

### It talks to you and remembers

```
macroa> what is the capital of France
macroa> remember my server IP is 192.168.1.100
macroa> what is my server IP?       ← answers instantly, no AI call, reads from memory
```

Facts you give it are stored locally in SQLite and persist forever. Close the terminal, come back in a month — it still knows.

### It runs shell commands on your behalf

```
macroa> !df -h
macroa> !ls -la /var/log
```

The `!` prefix bypasses AI entirely. Zero API cost, instant result. Shell output is captured and returned cleanly.

### It picks the right model without you thinking about it

Say something complex and it escalates automatically. Use keywords to force it:

```
macroa> think carefully about the tradeoffs of microservices   → Sonnet (P-cores)
macroa> use the best model to analyze this architecture        → Opus (GPU)
macroa> what time is it in Tokyo                               → Haiku (E-cores)
```

Routing itself always uses the cheapest model (Gemini Flash Lite). You only pay for heavy AI when you actually need it.

### It handles multi-step tasks end-to-end

```
macroa> write a comprehensive guide on home lab security hardening, networking, and monitoring
```

It automatically decomposes this into research steps, writing steps, and review steps — each running at the appropriate model tier — then combines everything into one coherent output.

### It remembers sessions by name, across restarts

```bash
macroa --session work
# ... have a full conversation ...
# close the terminal, reopen tomorrow
macroa --session work    # full context restored
```

Named sessions persist to disk. Without a name, sessions are ephemeral.

```bash
macroa sessions list
macroa sessions delete old-project
```

### It schedules commands to run automatically

```bash
macroa schedule add "morning-brief" "summarize my tasks for the day" "daily:08:00"
macroa schedule add "cleanup"       "!rm -rf /tmp/scratch"           "every:3600"
macroa schedule add "reminder"      "remind me to call John"         "once:1741600000"
macroa schedule list
macroa schedule delete <id>
```

The scheduler runs in the background and survives restarts. Supports `once:`, `every:`, `daily:`, and full 5-field cron expressions.

### It extends with tools you install

Tools are Python scripts that integrate any API or service. The OS routes to them automatically based on what you say.

```bash
macroa install /path/to/my_twilio_tool     # from local directory
macroa install https://github.com/user/repo  # from git
macroa tools list
macroa uninstall my_tool
```

Once installed, just say "call me" or "send a message" — the kernel figures out which tool to use.

### It has an HTTP API for other apps

```bash
macroa serve                          # starts at http://localhost:8000
macroa serve --host 0.0.0.0 --port 9000
```

```bash
curl -X POST localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"input": "what is my server IP", "session": "work"}'
```

Your scripts, automation, and other apps can all talk to the same AI OS with the same persistent sessions.

### It has a web dashboard

Open `http://localhost:8000/dashboard` after running `macroa serve`:

- Total runs, failure rate, plan calls
- Model tier distribution (how much went cheap vs expensive)
- Active sessions and their turn counts
- Scheduled task queue with next-run times
- Audit log of recent activity

---

## What it costs

| Operation | Cost | Why |
|-----------|------|-----|
| Shell command (`!ls`) | **$0** | No AI involved |
| Memory read/write | **$0** | Deterministic SQL |
| Routing (what do you want?) | ~$0.0001 | Gemini Flash Lite |
| Simple chat | Low | Haiku |
| "think carefully…" | Medium | Sonnet |
| "best model…" | Higher | Opus |

Routing and memory operations — which are the majority of calls in normal use — cost nearly nothing. You only pay Opus prices when you explicitly ask for it.

---

## Installation

```bash
git clone https://github.com/aniolowie/Macroa.git
cd Macroa
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# For the web API and dashboard:
pip install -e ".[web]"

cp .env.example .env
# Edit .env and add: OPENROUTER_API_KEY=sk-or-v1-...
```

---

## Usage

### REPL

```bash
macroa                           # ephemeral session
macroa --session work            # named persistent session
macroa --debug                   # show model tier and metadata
```

Built-in commands: `clear` · `debug` · `help` · `exit` / `quit` / `q`

### Single-shot

```bash
macroa run "!uname -a"
macroa run "remember my timezone is UTC+2"
macroa run "think carefully about the CAP theorem"
macroa run --session work "what did we discuss yesterday?"
```

### Sessions

```bash
macroa sessions list
macroa sessions delete <name>
```

### Scheduler

```bash
macroa schedule add "label" "command" "every:3600"
macroa schedule add "label" "command" "daily:09:00"
macroa schedule add "label" "command" "once:1741600000"
macroa schedule add "label" "command" "cron:0 9 * * 1"   # 9am every Monday
macroa schedule list
macroa schedule delete <id-prefix>
```

### Tools

```bash
macroa install /path/to/tool
macroa install https://github.com/user/repo
macroa install https://github.com/user/repo#subdir/tool
macroa tools list
macroa uninstall <name>
```

### Web server

```bash
macroa serve
macroa serve --host 0.0.0.0 --port 9000 --reload
```

API endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/run` | Run a command, get full response |
| `GET` | `/run/stream` | Run a command, stream via SSE |
| `GET` | `/sessions` | List named sessions |
| `DELETE` | `/sessions/{name}` | Delete a session |
| `POST` | `/schedule` | Add a scheduled task |
| `GET` | `/schedule` | List scheduled tasks |
| `DELETE` | `/schedule/{id}` | Remove a task |
| `GET` | `/audit/stats` | Aggregate usage stats |
| `GET` | `/audit/recent` | Recent run log |
| `GET` | `/dashboard` | Web dashboard UI |
| `GET` | `/health` | Health check |

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
    triggers=["do X", "run Y"],
    persistent=False,   # True = heartbeat() fires every 60s
    timeout=30,
)

class MyTool(BaseTool):
    def setup(self, drivers):
        pass  # called once on load

    def execute(self, intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
        # never raise — always return SkillResult
        return SkillResult(output="done", success=True, turn_id=intent.turn_id)

    def heartbeat(self, drivers):
        pass  # only called if persistent=True

    def teardown(self, drivers):
        pass  # called on shutdown
```

See `macroa/tools/examples/call_me/` for a complete reference implementation (Twilio phone call).

---

## Tests

```bash
pytest tests/ -v
pytest tests/ --cov=macroa --cov-report=term-missing
```

170 tests, zero external dependencies required (unit tests mock all LLM calls).

---

## Configuration

All settings are environment variables. Copy `.env.example` for the full reference.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | **Required** |
| `MACROA_MODEL_NANO` | `google/gemini-2.5-flash-lite` | Routing tier |
| `MACROA_MODEL_HAIKU` | `anthropic/claude-haiku-4-5` | E-cores tier |
| `MACROA_MODEL_SONNET` | `anthropic/claude-sonnet-4-6` | P-cores tier |
| `MACROA_MODEL_OPUS` | `anthropic/claude-opus-4-6` | GPU tier |
| `MACROA_CONTEXT_WINDOW` | `20` | Rolling turns kept in RAM |
| `MACROA_MEMORY_BACKEND` | `sqlite` | `sqlite` or `json` |
| `MACROA_TOOLS_DIR` | `~/.macroa/tools` | User-installed tools |
| `MACROA_HEARTBEAT_INTERVAL` | `60` | Seconds between heartbeat ticks |
| `MACROA_SESSIONS_DB_PATH` | `~/.macroa/sessions.db` | Named session store |
| `MACROA_SCHEDULER_DB_PATH` | `~/.macroa/scheduler.db` | Scheduled task store |
| `MACROA_SCHEDULER_POLL` | `10` | Seconds between scheduler ticks |
| `MACROA_AUDIT_DB_PATH` | `~/.macroa/audit.db` | Audit log |
| `MACROA_NETWORK_TIMEOUT` | `30` | HTTP timeout for tools |

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

### Model tiers — hardware analogy

| Tier | Hardware | Default model | Role |
|------|----------|---------------|------|
| `NANO` | Microcontroller | `google/gemini-2.5-flash-lite` | All routing, trivial ops |
| `HAIKU` | Efficiency cores (E-cores) | `anthropic/claude-haiku-4-5` | Lightweight tasks |
| `SONNET` | Performance cores (P-cores) | `anthropic/claude-sonnet-4-6` | Quality work |
| `OPUS` | GPU | `anthropic/claude-opus-4-6` | Heavy reasoning |

Escalation is automatic: if a skill returns `needs_reasoning=True`, the kernel promotes to the next tier and retries — up to two escalations per call.

### Memory layers

| Layer | Storage | Contents |
|-------|---------|----------|
| Working | RAM (`ContextManager` deque) | Current conversation turns |
| Semantic | SQLite `facts` table | User facts, preferences — expirable, confidence-scored |
| Episodic | SQLite `episodes` table | Session summaries, searchable by topic |
| Session | SQLite `sessions.db` | Named sessions + persisted context (survives restarts) |

---

## Project layout

```
macroa/
├── stdlib/
│   ├── schema.py          # All shared dataclasses
│   └── text.py            # Deterministic string utilities
├── config/
│   ├── settings.py        # Env/settings singleton
│   └── skill_registry.py  # Auto-discovery of skills/tools
├── drivers/
│   ├── llm_driver.py      # OpenRouter via openai SDK (+ streaming)
│   ├── shell_driver.py
│   ├── fs_driver.py
│   ├── memory_driver.py   # SQLite (facts + episodes) or JSON
│   └── network_driver.py  # HTTP client (stdlib)
├── skills/                # Kernel built-ins (deterministic)
│   ├── shell_skill.py
│   ├── file_skill.py
│   ├── memory_skill.py
│   └── chat_skill.py
├── kernel/
│   ├── context.py         # Rolling context window
│   ├── escalation.py      # Tier selection and promotion
│   ├── router.py          # Intent classification
│   ├── planner.py         # Task decomposition
│   ├── dispatcher.py      # Escalation loop
│   ├── sessions.py        # Named sessions + context persistence
│   ├── scheduler.py       # Cron/once/every/daily task scheduler
│   ├── events.py          # Pub/sub event bus
│   ├── audit.py           # Immutable run log
│   └── __init__.py        # Public: kernel.run()
├── tools/
│   ├── base.py            # BaseTool + ToolManifest
│   ├── registry.py        # Tool discovery and loading
│   ├── runner.py          # Timeout + error isolation
│   ├── installer.py       # Package manager (local + git)
│   ├── heartbeat.py       # Persistent tool daemon
│   └── examples/
│       └── call_me/       # Reference implementation (Twilio)
├── web/
│   ├── app.py             # FastAPI app (REST + SSE)
│   └── static/
│       └── dashboard.html # Web dashboard UI
└── cli/
    ├── renderer.py        # Rich terminal output
    └── main.py            # Click CLI + REPL
```

---

## Phase 3 (planned)

- Vector memory — semantic search via embeddings replacing substring match
- Streaming chat in the REPL — output appears token by token
- Multi-user web sessions with auth
- Cost tracking per session — dollar amounts in the dashboard
- Tool marketplace — curated index of installable tools
