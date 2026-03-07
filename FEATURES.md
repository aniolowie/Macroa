# Macroa Feature Reference — v0.3.0

## Core Pipeline

**Router** — Classifies every input into a skill + parameters using an LLM call (NANO). Has a keyword shortcut fast path for unambiguous triggers (shell, file, research, agent, etc.) that bypasses the LLM entirely. Memory skill is excluded from keyword shortcuts so parameter extraction (key/value/action) always goes through the LLM.

**Dispatcher** — Receives the routed intent, runs the appropriate skill, handles escalation if the skill signals it needs a stronger model (`needs_reasoning=True` → promotes up to 2 tiers).

**Context / Sessions** — Per-session message history stored in SQLite (`~/.macroa/sessions.db`). Named sessions persist across restarts (`macroa --session work`). Anonymous sessions are ephemeral.

---

## Skills

| Skill | What it does | Notes |
|---|---|---|
| `chat_skill` | General LLM conversation fallback | Streams token by token; injects identity, time, memory, and compacted episodes |
| `agent_skill` | Multi-step tool-calling loop | Up to 20 rounds; requires user confirmation for elevated commands |
| `memory_skill` | Store/retrieve/search/delete/list persistent facts | SQLite-backed, namespaced; exact reads skip LLM |
| `shell_skill` | Run shell commands directly | Hard-routed via `!` or `$` prefix; never calls LLM |
| `file_skill` | Read/write/list/exists for local files | $HOME-scoped path validation |
| `research_skill` | 4-phase multi-agent web research → cited markdown report | Saves to `~/.macroa/research/`; real-time progress via EventBus |
| `reminder_skill` | Add/list/delete reminders with timezone-aware scheduling | "in N minutes", "at HH:MM", "tomorrow at…"; fires `REMINDER_FIRED` event |

---

## Agent Tools (available inside `agent_skill`)

- `write_file` — create/overwrite any file
- `read_file` — read a file
- `run_command` — shell with tiered permission (safe/elevated/blocked); ELEVATED pauses for user confirmation with 30s timeout
- `remember` — write a fact to persistent memory
- `recall` — FTS5 search with BM25 ranking and confidence annotation
- `web_search` — DuckDuckGo via `ddgs` library, returns titles/URLs/snippets
- `fetch_url` — fetch and strip a web page to plain text (8000 char limit)
- `spawn_agent` — spawn a named subagent with its own objective, tier, and optional persona; results injected back into the calling turn

---

## AgentLoop (agent.py)

Context-aware tool-calling loop:
- Injects `now_context()` (live time + timezone) into every system prompt
- Retrieves relevant memory facts via `SemanticRetriever` (FTS5 + vector)
- Appends compacted episodic memories from `memory.get_episodes()`
- Runs up to 20 tool-calling rounds before forcing a final answer
- All context injection is wrapped in try/except — failures never break agent execution

---

## Multi-Agent Orchestration (multi_agent.py)

`MultiAgentCoordinator` runs `AgentTask` objects respecting a dependency graph (DAG):

- **Wave execution** — topological sort into parallel waves; independent tasks run in concurrent threads
- **Dependency injection** — dependent tasks receive predecessor output as injected context
- **Failure propagation** — failed dependency marks all dependents failed automatically
- **Synthesis** — HAIKU model merges all successful outputs into one coherent response; single successful result returned directly without extra LLM call
- `_MAX_AGENTS = 8`, `_AGENT_TIMEOUT = 120s` safety caps
- Public API: `kernel.run_agents(tasks, original_request, session_id)`

**`AgentTask` fields:** `name`, `objective`, `model_tier` (default SONNET), `depends_on[]`, `persona`

---

## Research Pipeline (research_skill)

Four phases:
1. **Plan** (SONNET) — decomposes query into 3–5 investigation trajectories
2. **Investigate** (HAIKU × N) — each subagent runs OODA-guided web tool loop (`web_search` + `fetch_url`); forced summarize if max rounds hit without `<findings>` XML
3. **Verify** (HAIKU) — cross-checks all findings, flags low-confidence claims
4. **Synthesize** (SONNET) — combines verified findings into a citation-rich markdown report

**Live feed** — CLI prints real-time progress (phases, subagent starts, tool calls, source counts) via EventBus pub/sub.

---

## Memory

### Storage
- SQLite backend (`~/.macroa/memory.db`)
- `facts` table: namespace + key/value + confidence + expiry + FTS5 virtual table
- `episodes` table: timestamped summaries; FTS5-searchable
- Schema versioning / migrations

### Retrieval
- **Exact key** — `memory.get(ns, key)` → direct SQL lookup; no LLM, no cost
- **FTS5** — `memory.search_fts(query, limit)` → BM25-ranked keyword match
- **Semantic** — `SemanticRetriever.retrieve(query)` → FTS5 bucket + cosine similarity bucket, deduplicated by key

### Vector Memory (EmbeddingStore)
- SQLite-backed: `~/.macroa/memory/embeddings.db`
- Schema: `embeddings(id PK, namespace, key, model, vector BLOB, created_at)`
- Embeddings stored as packed float32 (struct.pack) — ~6 KB per 1536-dim vector; no numpy required
- **Auto-indexing on write** — `MemoryDriver.set_fact()` queues a background embedding via daemon thread; main thread never waits
- **LRU cache** (128 entries) on query embeddings — repeated queries hit RAM, not the API
- `_cosine(a, b)` — pure Python dot-product / magnitude; returns 0.0 on zero vectors
- Degrades gracefully to FTS5-only if embedding API is unavailable

### Context Compaction (ContextCompactor)
- `ContextManager.on_evict` hook fires when the rolling window evicts an entry
- Compactor summarises each evicted entry into a 1–2 sentence episodic memory using NANO
- Runs in a daemon thread — never blocks the main request
- Compacted episodes injected into `chat_skill` and `agent_skill` prompts under "Earlier in this conversation (compacted)"

---

## Identity Layer

Three files loaded from `~/.macroa/` on every boot:
- `IDENTITY.md` — agent name, nature, vibe, emoji
- `USER.md` — user name, timezone, preferences
- `SOUL.md` — values, behaviour limits, safety guardrails

First boot (no `IDENTITY.md`): bootstrap mode — agent introduces itself and writes the files via `write_file`. Injected into `chat_skill` and `agent_skill` system prompts.

**`_SAFETY_SECTION`** — explicit guardrails against destructive commands, data exfiltration, and power-seeking; present in every system prompt.

**`_build_runtime_section()`** — Macroa version, OS, Python, workspace path in every system prompt.

---

## Real-time Identity & Reminders

**`now_context(memory)`** (`macroa/kernel/clock.py`) — returns a formatted current-time string injected at the top of every `chat_skill` and `agent_skill` system prompt.

**`get_user_timezone(memory)`** — checks `memory.get("user", "timezone")`, parses `/etc/localtime` symlink, falls back to `"UTC"`.

**Reminder CRUD** — natural-language scheduling: "in 30 minutes", "at 14:00", "tomorrow at 9am"; timezone-aware via `_TZ_ALIASES`.

**`Events.REMINDER_FIRED`** — emitted by scheduler when a reminder task fires. REPL prints `⏰ Reminder [HH:MM]  message` banner.

---

## Streaming

**REPL streaming** — `LLMDriver.stream()` yields chunks; `chat_skill` uses it when `stream_callback` is set on `DriverBundle`; REPL prints each chunk as it arrives via Rich `console.print(chunk, end="")`.

**SSE streaming** — `GET /run/stream` bridges the sync kernel thread with async FastAPI via a `threading.Thread` + `asyncio.Queue`. The `_on_chunk` callback puts chunks into the queue; `_sse_generator` consumes with `asyncio.sleep(0.02)` polling. Truly streamed — not a single flush after completion.

**`stream_callback`** — on `DriverBundle` as `Callable[[str], None] | None = None`; per-request copy via `dataclasses.replace(drivers, stream_callback=cb)`.

---

## Cost Tracking

- **`AuditEntry`** gains `prompt_tokens`, `completion_tokens`, `cost_usd` fields
- **Blended $/M dict** in `kernel/__init__.py` keyed on exact OpenRouter model IDs
- **`_compute_cost()`** calculates actual spend per turn using prompt/completion token counts
- **Debug mode** shows `{N}tok  ${cost:.5f}` alongside skill and tier
- **`GET /audit/recent`** response includes token/cost fields per entry

---

## Daemon Mode

Always-on background process that keeps the scheduler, heartbeat, and web API alive without an open terminal.

- **PID file** at `~/.macroa/daemon.pid`; stale files auto-cleaned by `is_running()`
- **Status JSON** at `~/.macroa/daemon_status.json` — updated every 30s (pid, scheduler_tasks, web_port, uptime)
- **Daemon entry point** — initialises kernel + starts uvicorn + heartbeat loop; handles `SIGTERM`/`SIGINT` gracefully
- **CLI**: `macroa daemon start [--port PORT] [--no-web]`, `macroa daemon stop`, `macroa daemon status`
- **REPL banner** shows live daemon state: `daemon: running  tasks: N  web: :8000`

---

## Channel Adapters

**`BaseAdapter`** ABC (`macroa/channels/base.py`):
- Per-user kernel sessions via `kernel.resolve_session(f"{platform}_{user_id}")`
- Daemon thread lifecycle: `start()`, `stop()`, `running` property
- Error recovery: network errors retry; fatal errors (401) raise `AdapterError` immediately

**`TelegramAdapter`** (`macroa/channels/telegram.py`):
- Long-polling via `httpx` (`GET /getUpdates?offset=<next>&timeout=30`)
- ACKs via offset increment after processing
- Built-in commands: `/start`, `/help`, `/clear`
- Optional user allowlist (`--allow UID`)
- 4096-char message splitting on newlines
- `validate_token()` — calls `getMe`, raises `AdapterError` on 401

**`DiscordAdapter`** (`macroa/channels/discord.py`):
- Gateway WebSocket (`websockets` optional dep) with heartbeat task; REST polling fallback
- Handles: HELLO (op 10), READY, MESSAGE_CREATE events
- Built-in commands: `/macroa help`, `/macroa clear`
- Channel filter (`--channel ID`) and user allowlist (`--allow UID`)
- 2000-char message splitting; auto-reconnects on gateway drop

---

## Webhooks

**`WebhookConfig`** — name, secret (auto-generated `secrets.token_urlsafe(24)`), template string.

**`WebhookStore`** — SQLite-backed registry of inbound webhook triggers; CRUD operations.

**`render_template()`** — `{{body}}`, `{{field}}`, `{{field.nested}}` placeholder substitution against the incoming JSON body.

**`POST /webhook/{name}?key=SECRET`** — validates key, renders template, calls `kernel.run()`, returns `RunResponse`.

**Management endpoints**: `POST/GET/DELETE /webhooks`.

---

## Scheduler

Cron-equivalent daemon thread. Specs: `once:<ts>`, `every:<secs>`, `daily:<HH:MM>`, `cron:<5-field>`. All tasks run via `kernel.run()` so they appear in the audit log. Reminder tasks emit `Events.REMINDER_FIRED` on completion.

---

## Audit Log

Every `kernel.run()` call recorded to `~/.macroa/audit.db`:
- input, skill used, success/failure, timestamp
- `prompt_tokens`, `completion_tokens`, `cost_usd` (v0.3.0+)
- Not wiped when memory is cleared
- Exposed via `GET /audit/recent` and `GET /audit/stats`

---

## Tool System

- User-installable tools from local path or git URL (`macroa install <path|url>`)
- Tools live in `~/.macroa/tools/`, auto-loaded at startup
- `BaseTool` base class with `setup()` + `execute()` + `heartbeat()` + `teardown()`
- Optional persistent/background tools with configurable heartbeat ticks
- Tool `.env` files auto-loaded — secrets isolated per tool
- Example tool: `call_me` (Twilio phone call)
- CLI: `macroa install/uninstall/tools list`

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `macroa` | Interactive REPL |
| `macroa run "<input>"` | Single-shot command |
| `macroa --session <name>` | Named session |
| `macroa --debug` | Debug metadata per turn |
| `macroa setup` | Re-run setup wizard |
| `macroa serve` | HTTP API + dashboard |
| `macroa daemon start` | Spawn background daemon |
| `macroa daemon stop` | Stop daemon gracefully |
| `macroa daemon status` | Show daemon state |
| `macroa telegram --token T` | Start Telegram adapter |
| `macroa discord --token T` | Start Discord adapter |
| `macroa sessions list/delete` | Manage named sessions |
| `macroa schedule add/list/delete` | Manage scheduled tasks |
| `macroa tools list` | List installed tools |
| `macroa install <path\|url>` | Install a tool |
| `macroa uninstall <name>` | Remove a tool |

---

## HTTP API

FastAPI + real SSE streaming. All endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/run` | Run a command, get full response |
| `GET` | `/run/stream` | Real token-streaming via SSE |
| `GET` | `/sessions` | List named sessions |
| `DELETE` | `/sessions/{name}` | Delete a session |
| `POST` | `/schedule` | Add a scheduled task |
| `GET` | `/schedule` | List scheduled tasks |
| `DELETE` | `/schedule/{id}` | Remove a scheduled task |
| `GET` | `/audit/stats` | Aggregate usage stats |
| `GET` | `/audit/recent` | Recent run log with tokens + cost |
| `POST` | `/webhooks` | Register a webhook |
| `GET` | `/webhooks` | List all webhooks |
| `DELETE` | `/webhooks/{name}` | Delete a webhook |
| `POST` | `/webhook/{name}` | Trigger a webhook |
| `GET` | `/dashboard` | Web dashboard UI |
| `GET` | `/health` | Health check |

Optional dep: `pip install macroa[web]`.

---

## LLMDriver

**`complete(messages, model, temperature, response_format)`** — blocking completion call via OpenRouter.

**`stream(messages, model, temperature, callback)`** — streaming completion; calls `callback(chunk)` for each token.

**`embed(texts, model="openai/text-embedding-3-small")`** — calls OpenRouter Embeddings API; returns `list[list[float]]` sorted by index; raises `LLMDriverError` on failure; empty input returns `[]` without an API call.

---

## Config / Settings

All settings from env vars or `.env` files. Priority: shell env → project `.env` (CWD only) → `~/.macroa/.env`. Four model tiers: NANO, HAIKU, SONNET, OPUS — all individually configurable. Strips accidental `openrouter/` prefix from model IDs.

---

## Tests

270+ tests across: router, skills, drivers, memory, semantic/vector memory (`_cosine`, `EmbeddingStore`, `SemanticRetriever`, `LLMDriver.embed`, `MemoryDriver` embedding integration), scheduler, sessions, audit, research pipeline, tool installer, sudo classifier, identity, web API, webhooks, multi-agent orchestration, daemon lifecycle, channel adapters.

All LLM and embedding calls are mocked. Test suite runs fully offline.
