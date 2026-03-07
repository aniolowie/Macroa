# Changelog

All notable changes to Macroa are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.3.0] — 2026-03-07

### Added — Streaming

- **Token-by-token REPL output** — `LLMDriver.stream()` yields chunks; `chat_skill` uses it when `stream_callback` is set on `DriverBundle`; REPL prints each chunk as it arrives via Rich `console.print(chunk, end="")`
- **Real SSE streaming** — `GET /run/stream` now uses a thread + `asyncio.Queue` bridge so the LLM response is truly streamed to the browser instead of sent as a single chunk after completion
- **`stream_callback`** on `DriverBundle` — per-request copy via `dataclasses.replace(drivers, stream_callback=cb)`; other skills ignore it safely

### Added — Cost Tracking

- **Per-turn dollar amounts** — `AuditEntry` gains `prompt_tokens`, `completion_tokens`, `cost_usd`; recorded in `audit.db` after every `kernel.run()`
- **Blended $/M dict** in `kernel/__init__.py` keyed on exact OpenRouter model IDs; `_compute_cost()` calculates actual spend per turn
- **Debug mode shows cost** — REPL `debug` toggle now shows `{N}tok  ${cost:.5f}` alongside skill and tier
- **`GET /audit/recent`** — API response now includes token/cost fields per entry

### Added — Context Compaction

- **`ContextCompactor`** (`macroa/memory/compactor.py`) — summarises evicted context entries into 1–2 sentence episodic memories using NANO LLM; runs in a daemon thread (non-blocking)
- **`ContextManager.on_evict`** hook — called with each evicted `ContextEntry`; compactor is wired as the handler on session creation
- **`_evict_oldest_unpinned()`** — returns the evicted entry (was `None`) so the hook can act on it
- **Episodes injected into chat prompts** — `_build_system()` in `chat_skill` appends a "## Earlier in this conversation (compacted)" section when episodic memories exist for the session

### Added — Webhook Triggers

- **`WebhookConfig` + `WebhookStore`** (`macroa/web/webhooks.py`) — SQLite-backed registry of inbound HTTP webhook triggers; auto-generated `secrets.token_urlsafe(24)` secret per webhook
- **`render_template()`** — `{{body}}`, `{{field}}`, `{{field.nested}}` placeholder substitution against the incoming JSON body
- **`POST /webhook/{name}?key=`** — validates secret key, renders template, calls `kernel.run()`, returns `RunResponse`
- **`POST/GET/DELETE /webhooks`** — webhook management API endpoints
- **`WebhookCreateRequest` / `WebhookInfo`** Pydantic models in `web/app.py`

### Added — Real-time Identity & Reminders

- **`now_context(memory)`** (`macroa/kernel/clock.py`) — returns a formatted current-time string injected at the top of every `chat_skill` system prompt; resolves timezone from memory → `/etc/localtime` → UTC
- **`get_user_timezone(memory)`** — checks `memory.get("user", "timezone")`, parses `/etc/localtime` symlink, falls back to `"UTC"`
- **`_DEFAULT_SOUL`** — written once to `~/.macroa/identity/SOUL.md`; sets direct, non-sycophantic, privacy-first personality
- **`_SAFETY_SECTION`** — explicit guardrails against destructive commands, data exfiltration, and power-seeking in every system prompt
- **`_build_runtime_section()`** — Macroa version, OS, Python, workspace path in every system prompt
- **Reminder CRUD** (`reminder_skill.py` rewrite) — add/list/delete reminders; timezone-aware (`_TZ_ALIASES`, "in N minutes/hours", "tomorrow at HH:MM"); uses `schedule_add()` under the hood
- **`Events.REMINDER_FIRED`** — emitted by scheduler when a reminder task fires
- **REPL reminder notifications** — `_on_reminder_fired()` prints a `⏰ Reminder [HH:MM]  message` banner

### Added — Agent & Tool Improvements

- **Agent is now context-aware** — `AgentLoop.run()` injects `now_context()`, retrieved memory facts, and compacted episodes into the system prompt (was previously blind to all three)
- **`_recall` uses FTS5** — upgraded from deprecated `memory.search()` to `search_fts()` with BM25 ranking and confidence annotation
- **`LLMDriver.embed(texts, model)`** — calls the OpenRouter Embeddings API; returns `list[list[float]]`; raises `LLMDriverError` on failure; used by EmbeddingStore

### Added — Daemon Mode

- **`macroa/kernel/daemon.py`** — `start()`, `stop()`, `is_running()`, `read_status()` for controlling a detached background process
- **PID file** at `~/.macroa/daemon.pid`; stale files auto-cleaned by `is_running()`
- **Status JSON** at `~/.macroa/daemon_status.json` updated every 30 s (pid, scheduler_tasks, web_port, uptime)
- **Daemon entry point** — `python -m macroa.kernel.daemon`; initialises kernel + starts uvicorn + heartbeat loop; handles `SIGTERM`/`SIGINT` gracefully
- **`macroa daemon start [--port PORT] [--no-web]`** — spawns daemon and confirms it started
- **`macroa daemon stop`** — sends `SIGTERM`, waits up to 5 s for graceful exit
- **`macroa daemon status`** — shows PID, active tasks, web port, uptime
- **REPL banner shows daemon status** — "daemon: running  tasks: N  web: :8000" or "daemon: offline  (macroa daemon start)"

### Added — Channel Adapters

- **`macroa/channels/base.py`** — `BaseAdapter` ABC: per-user kernel sessions, daemon thread lifecycle, error recovery
- **`TelegramAdapter`** (`macroa/channels/telegram.py`) — Telegram Bot API long-polling; `/start`, `/help`, `/clear` built-in commands; optional user allowlist; 4096-char message splitting; auto-retry on network errors; 401 → `AdapterError` (fast fail)
- **`DiscordAdapter`** (`macroa/channels/discord.py`) — Discord Gateway (WebSocket) when `websockets` is installed; REST polling fallback; channel filter; `/macroa help|clear` commands; auto-reconnect on gateway drop; 2000-char message splitting
- **`macroa telegram --token TOKEN`** — start Telegram adapter (blocks until Ctrl-C; or pass `MACROA_TELEGRAM_TOKEN` env var)
- **`macroa discord --token TOKEN [--channel ID]`** — start Discord adapter

### Added — Multi-Agent Orchestration

- **`MultiAgentCoordinator`** (`macroa/kernel/multi_agent.py`) — runs `AgentTask` objects respecting a dependency graph (DAG); independent tasks execute in parallel threads; dependent tasks receive predecessor output as injected context
- **`AgentTask`** — `name`, `objective`, `model_tier`, `depends_on[]`, `persona`; subagent gets ephemeral session derived from parent
- **`AgentResult`** — `output`, `success`, `elapsed_ms`, `error`
- **Wave execution** — topological sort into parallel waves; failed dependency → all dependents marked failed automatically; `_AGENT_TIMEOUT = 120 s` safety cap per agent
- **HAIKU synthesizer** — merges multi-agent outputs into one coherent response
- **`spawn_agent` tool** in `tool_defs.py` — agents can spawn named subagents from within a tool-calling loop; accepts `name`, `objective`, `tier`, `persona`
- **`kernel.run_agents(tasks, original_request, session_id)`** — public API for multi-agent execution
- **`macroa daemon` group updated** with `macroa daemon start/stop/status` help text

### Added — Vector Memory

- **`EmbeddingStore`** (`macroa/memory/semantic.py`) — SQLite-backed vector store; `queue_embed()` fires background daemon thread; pure Python cosine similarity (`_cosine()`) — no numpy required; float32 struct.pack/unpack (~6 KB per 1536-dim vector); LRU cache (128 entries) for query embeddings
- **`SemanticRetriever`** — two-bucket merge: FTS5 keyword results + embedding similarity results; deduplicated by key
- **Auto-embedding on write** — `MemoryDriver.set_embedding_store(store)` hooks `set_fact()` to queue background embedding; graceful no-op when store is absent
- **Kernel wires it up** — `EmbeddingStore` created at startup using `~/.macroa/memory/embeddings.db`; silently degrades to FTS5 if embedding API unavailable

### Fixed

- **Agent system prompt** — was empty context (no time, no memory, no episodes); now includes all three
- **`_recall` tool** — was using deprecated `memory.search()`; now uses `search_fts()` with BM25 ranking
- **`GET /run/stream`** — was fake chunking of a completed response; now real token-streaming via `stream_callback`
- **`test_bootstrap_custom_file`** — updated to `result.startswith(...)` after identity prompt grew
- **`test_empty_identity_file_returns_fallback`** — renamed + updated after `_FALLBACK` was removed
- **`test_chat_skill_no_memory_no_injection`** — added `memory.get_episodes.return_value = []` to prevent MagicMock truthy from injecting a spurious episodes section

---

## [0.2.9] — 2026-03-06

### Fixed
- **Web search backend** — replaced broken DuckDuckGo HTML scraper (blocked server-side) with the `ddgs` library, which uses DDG's actual API; search now returns real results
- **`ddgs>=9.0`** added as a core dependency

---

## [0.2.8] — 2026-03-06

### Added
- **Multi-agent research system** (`macroa/research/`) — four-phase orchestrated pipeline:
  - **Phase 1 — Orchestrate** (SONNET): decomposes query into 3–5 independent investigation trajectories
  - **Phase 2 — Investigate** (HAIKU × N): each subagent runs an OODA-guided web tool loop (`web_search` + `fetch_url` only), emitting `<findings>` + `<citations>` XML
  - **Phase 3 — Verify** (HAIKU): cross-checks all findings, flags low-confidence claims
  - **Phase 4 — Synthesize** (SONNET): combines verified findings into a citation-rich markdown report
- **`research_skill`** — routes "research", "investigate", "look into", "compile a report" etc.; saves report to `~/.macroa/research/<timestamp>-<slug>.md`
- **`web_search` + `fetch_url` agent tools** — DuckDuckGo HTML search and URL fetch available to all agent turns, not just research
- **Memory routing fix** — `memory_skill` excluded from keyword shortcut so "remember that my X is Y" correctly routes through LLM parameter extraction
- **Agent tool cap raised** — `_MAX_ROUNDS` increased from 10 → 20 to support multi-source research

---

## [0.2.7] — 2026-03-06

### Fixed
- **`agent_skill` registry warning** — stub `run()` added so skill registry loads the module without "run() is missing" warning
- **Tool capabilities in system prompt** — `_CAPABILITIES_SECTION` always appended to post-onboarding system prompt
- **Shell skill UX** — missing command now returns a helpful prompt instead of a bare error

---

## [0.2.5] — 2026-03-06

### Added
- **Agent skill** — agentic tool-calling loop powered by OpenAI function calling. Tools: `write_file`, `read_file`, `run_command`, `remember`, `recall`. Runs until the LLM stops invoking tools or hits a 20-round safety cap
- **sudo permission tier** — every `run_command` call is classified SAFE / ELEVATED / BLOCKED before execution; ELEVATED commands pause and ask the user with a 30 s SIGALRM timeout
- **First-boot agent mode** — kernel overrides all turns to `agent_skill` until `IDENTITY.md` is written
- **Router improvements** — keyword shortcut fast path, HAIKU retry on low confidence, few-shot examples, `ROUTE_DECISION` event

---

## [0.2.4] — 2026-03-06

### Fixed
- **Router JSON parse failure** — `response_format={"type": "json_object"}` forces structured output; eliminates routing fallback to `chat_skill`
- **Markdown fence stripping** — `_extract_json()` strips ` ```json ` fences before parsing

### Added
- **Identity layer** — `build_system_prompt()` loads `IDENTITY.md`, `USER.md`, `SOUL.md`; bootstrap writes `BOOTSTRAP.md` on first boot
- **Memory-aware chat** — `chat_skill` searches relevant facts and appends them to the system prompt

---

## [0.2.3] — 2026-03-06

### Fixed
- Include `macroa/web/static/*` in `pyproject.toml` package-data so `dashboard.html` is bundled in the wheel

---

## [0.2.1] — 2026-03-06

### Added
- **Setup wizard** — interactive first-run wizard; writes to `~/.macroa/.env`
- **Persistent user config** — settings survive pip reinstalls and work across projects
- **Startup banner** — Rich panel with version, greeting, model stack, and lifetime activity summary
- **`macroa setup`** — re-run wizard at any time

---

## [0.2.0] — 2026-03-06

### Added
- **Named sessions** — `--session <name>` resolves to stable UUIDs; persisted to SQLite
- **Scheduler** — `once:`, `every:`, `daily:`, and 5-field cron expressions
- **FastAPI web layer** — REST API + SSE streaming + web dashboard
- **`macroa serve`** CLI command
- **Tool package manager** — `macroa install <path|url>`, `macroa uninstall`, `macroa tools list`
- **Thread safety** — `threading.Lock` on kernel session dict
- **Context persistence** — context entries auto-saved and restored on session resume

---

## [0.1.0] — 2026-02-01

### Added
- **Kernel** — Router (NANO), Planner (NANO), Dispatcher with escalation loop, Combiner (HAIKU)
- **4-tier model system** — NANO / HAIKU / SONNET / OPUS
- **Drivers** — LLM (OpenRouter via openai SDK), Shell, Filesystem, Memory (SQLite v2), Network
- **Skills** — `shell_skill`, `file_skill`, `memory_skill`, `chat_skill`
- **Tools system** — `BaseTool`, `ToolManifest`, `ToolRegistry`, `ToolRunner`, `HeartbeatManager`
- **Memory v2** — `facts` + `episodes` tables with confidence, source, expiry; schema versioning
- **EventBus** — thread-safe pub/sub singleton
- **AuditLog** — every `kernel.run()` automatically recorded
- **CLI** — REPL and single-shot with Rich rendering
- 108 unit tests, zero external dependencies required
